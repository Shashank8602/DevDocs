"""Vector leg of retrieval: approximate kNN on `embedding`, with the isolation filter applied inside the kNN."""
import uuid

from elasticsearch import NotFoundError

from app.retrieval.bm25 import Hit, scope_filter, to_hits
from app.search import es_client
from app.search.index_manager import index_name


async def vector_search(
    fingerprint: str, project_id: uuid.UUID, ready_file_ids: list[uuid.UUID],
    query_vector: list[float], k: int = 10,
) -> list[Hit]:
    if not ready_file_ids:
        return []
    try:
        resp = await es_client.es.search(
            index=index_name(fingerprint), size=k,
            knn={
                "field": "embedding", "query_vector": query_vector, "k": k,
                "num_candidates": max(100, k * 10),
                "filter": scope_filter(project_id, ready_file_ids),
            },
            source_includes=["file_id", "file_path", "content"],
        )
    except NotFoundError:
        return []
    return to_hits(resp)
