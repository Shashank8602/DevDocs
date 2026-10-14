"""BM25 leg of retrieval: stopword-stripped terms matched on `content`, plus the file path and the
heading / function name, always scoped to one project and its READY files.

Matching the path and section lets "what does auth.py do" or "the upload rules" find a chunk whose body
never says so. Measured on 428 questions (eval/bm25_variants.py): ranking share +4.6 (tuning set) / +4.1 (held-out) points, MRR
+2, and the right file is in the BM25 top 10 for 92% / 96% of questions instead of 87% / 83%."""
import uuid
from dataclasses import dataclass

from elasticsearch import NotFoundError

from app.retrieval.query import keyword_query
from app.search import es_client
from app.search.index_manager import index_name


@dataclass
class Hit:
    chunk_id: str
    file_id: str
    file_path: str
    score: float
    content: str


def scope_filter(project_id: uuid.UUID, ready_file_ids: list[uuid.UUID]) -> list[dict]:
    """The isolation filter: every query carries it, inside the query itself (not as a post-filter)."""
    return [
        {"term": {"project_id": str(project_id)}},
        {"terms": {"file_id": [str(f) for f in ready_file_ids]}},
    ]


def to_hits(resp: dict) -> list[Hit]:
    return [
        Hit(h["_id"], h["_source"]["file_id"], h["_source"]["file_path"], h["_score"], h["_source"]["content"])
        for h in resp["hits"]["hits"]
    ]


async def bm25_search(
    fingerprint: str, project_id: uuid.UUID, ready_file_ids: list[uuid.UUID], query: str, k: int = 10,
) -> list[Hit]:
    if not ready_file_ids or not query.strip():
        return []
    terms = keyword_query(query)
    try:
        resp = await es_client.es.search(
            index=index_name(fingerprint), size=k,
            query={"bool": {
                "should": [
                    {"match": {"content": terms}},
                    {"match": {"path_text": terms}},
                    {"match": {"section": terms}},
                ],
                "minimum_should_match": 1,
                "filter": scope_filter(project_id, ready_file_ids),
            }},
            source_includes=["file_id", "file_path", "content"],
        )
    except NotFoundError:
        return []
    return to_hits(resp)
