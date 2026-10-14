"""Writes and deletes chunk documents in Elasticsearch. Postgres stays the source of truth:
callers write Postgres first, then index here, and only then mark the file `ready`."""
import hashlib
import uuid
from dataclasses import dataclass

from elasticsearch import NotFoundError
from elasticsearch.helpers import async_bulk

from app.search import es_client
from app.search.index_manager import index_name


@dataclass
class IndexDoc:
    chunk_id: uuid.UUID
    project_id: uuid.UUID
    file_id: uuid.UUID
    file_path: str
    file_type: str
    content: str
    embedding: list[float]
    section: str | None = None  # heading path / function name; searchable in BM25


async def index_chunks(index: str, docs: list[IndexDoc]) -> None:
    """Bulk index the docs (`_id` = chunk id). Raises if any doc fails; visible to search on return."""
    if not docs:
        return
    actions = (
        {
            "_op_type": "index", "_index": index, "_id": str(d.chunk_id),
            "_source": {
                "project_id": str(d.project_id), "file_id": str(d.file_id), "file_path": d.file_path,
                "file_type": d.file_type, "content": d.content, "embedding": d.embedding,
                "path_text": d.file_path, "section": d.section,
            },
        }
        for d in docs
    )
    await async_bulk(es_client.es, actions, refresh="wait_for", raise_on_error=True)


async def _delete_by_query(fingerprint: str, term: dict) -> int:
    try:
        resp = await es_client.es.delete_by_query(
            index=index_name(fingerprint), query={"term": term}, refresh=True, conflicts="proceed",
        )
    except NotFoundError:  # the fingerprint's index was never created: nothing to delete
        return 0
    return resp["deleted"]


async def delete_file_docs(fingerprint: str, file_id: uuid.UUID) -> int:
    return await _delete_by_query(fingerprint, {"file_id": str(file_id)})


async def delete_project_docs(fingerprint: str, project_id: uuid.UUID) -> int:
    return await _delete_by_query(fingerprint, {"project_id": str(project_id)})


async def count_docs(fingerprint: str, project_id: uuid.UUID | None = None) -> int:
    query = {"term": {"project_id": str(project_id)}} if project_id else {"match_all": {}}
    try:
        return (await es_client.es.count(index=index_name(fingerprint), query=query))["count"]
    except NotFoundError:
        return 0


async def count_by_file(fingerprint: str, project_id: uuid.UUID, size: int) -> dict[str, int]:
    """`terms` aggregation on file_id for one project: {file_id: ES chunk count}."""
    try:
        resp = await es_client.es.search(
            index=index_name(fingerprint), size=0, query={"term": {"project_id": str(project_id)}},
            aggs={"by_file": {"terms": {"field": "file_id", "size": size}}},
        )
    except NotFoundError:
        return {}
    return {b["key"]: b["doc_count"] for b in resp["aggregations"]["by_file"]["buckets"]}


async def get_vectors(fingerprint: str, project_id: uuid.UUID, file_id: uuid.UUID, size: int = 1000) -> dict[str, list[float]]:
    """{chunk_id: embedding} for one file of one project (both ids are filtered, never just the file)."""
    try:
        resp = await es_client.es.search(
            index=index_name(fingerprint), size=size, source_includes=["embedding"],
            query={"bool": {"filter": [{"term": {"project_id": str(project_id)}}, {"term": {"file_id": str(file_id)}}]}},
        )
    except NotFoundError:
        return {}
    return {h["_id"]: h["_source"]["embedding"] for h in resp["hits"]["hits"] if "embedding" in h["_source"]}


def content_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def vectors_by_content(
    fingerprint: str, project_id: uuid.UUID, file_id: uuid.UUID, size: int = 1000,
) -> dict[str, list[float]]:
    """{sha256(content): embedding} of one file's current docs. When a file is modified most of its chunks
    keep their text, so their vectors can be reused instead of paying for the same embedding again."""
    try:
        resp = await es_client.es.search(
            index=index_name(fingerprint), size=size, source_includes=["content", "embedding"],
            query={"bool": {"filter": [{"term": {"project_id": str(project_id)}}, {"term": {"file_id": str(file_id)}}]}},
        )
    except NotFoundError:
        return {}
    return {
        content_key(h["_source"]["content"]): h["_source"]["embedding"]
        for h in resp["hits"]["hits"] if "embedding" in h["_source"] and "content" in h["_source"]
    }
