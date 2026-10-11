"""Removes ES docs whose file no longer exists in Postgres.

Run from backend/:  python -m app.search.reconcile
Retrieval never trusts ES alone (it intersects with Postgres READY ids), so orphans can't leak into
answers; this just keeps the index tidy and the ES Inspector's Orphans list empty."""
import asyncio
import uuid

from elasticsearch import NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import File
from app.providers.embeddings import get_embedding_provider
from app.search import es_client
from app.search.index_manager import index_name

_PAGE = 500


async def es_file_ids(fingerprint: str, project_id: uuid.UUID | None = None) -> dict[str, int]:
    """Every file_id present in the index (optionally one project's) with its doc count."""
    query = {"term": {"project_id": str(project_id)}} if project_id else {"match_all": {}}
    found: dict[str, int] = {}
    after = None
    while True:
        composite = {"size": _PAGE, "sources": [{"file": {"terms": {"field": "file_id"}}}]}
        if after:
            composite["after"] = after
        try:
            resp = await es_client.es.search(
                index=index_name(fingerprint), size=0, query=query, aggs={"files": {"composite": composite}},
            )
        except NotFoundError:
            return found
        agg = resp["aggregations"]["files"]
        for b in agg["buckets"]:
            found[b["key"]["file"]] = b["doc_count"]
        after = agg.get("after_key")
        if not agg["buckets"] or after is None:
            return found


async def find_orphans(session: AsyncSession, fingerprint: str, project_id: uuid.UUID | None = None) -> dict[str, int]:
    """ES docs grouped by file_id, for files Postgres no longer has."""
    in_es = await es_file_ids(fingerprint, project_id)
    if not in_es:
        return {}
    known = {str(i) for i in (await session.scalars(select(File.id).where(File.id.in_([uuid.UUID(f) for f in in_es])))).all()}
    return {fid: n for fid, n in in_es.items() if fid not in known}


async def reconcile(session: AsyncSession, fingerprint: str) -> dict[str, int]:
    """Delete orphaned docs. Returns {file_id: deleted count}."""
    orphans = await find_orphans(session, fingerprint)
    removed: dict[str, int] = {}
    for file_id in orphans:
        try:
            resp = await es_client.es.delete_by_query(
                index=index_name(fingerprint), query={"term": {"file_id": file_id}}, refresh=True, conflicts="proceed",
            )
        except NotFoundError:
            continue
        removed[file_id] = resp["deleted"]
    return removed


async def _main() -> None:
    fingerprint = get_embedding_provider().fingerprint
    async with SessionLocal() as session:
        removed = await reconcile(session, fingerprint)
    total = sum(removed.values())
    print(f"index {index_name(fingerprint)}: removed {total} orphaned doc(s) from {len(removed)} missing file(s)")
    for file_id, n in removed.items():
        print(f"  {file_id}: {n}")
    await es_client.es.close()


if __name__ == "__main__":
    asyncio.run(_main())
