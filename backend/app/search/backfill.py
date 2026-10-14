"""Fills `path_text` and `section` on ES docs indexed before those fields existed. No embeddings are
computed (the vectors stay as they are), so this costs no API calls.

Run from backend/:  python -m app.search.backfill"""
import asyncio

from elasticsearch.helpers import async_bulk
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, File, Project
from app.search import es_client
from app.search.index_manager import ensure_index, index_name

_PAGE = 1000


async def backfill(fingerprint: str, dims: int) -> int:
    await ensure_index(fingerprint, dims)
    index, total, offset = index_name(fingerprint), 0, 0
    async with SessionLocal() as session:
        while True:
            rows = (await session.execute(
                select(Chunk.id, Chunk.section, File.file_path)
                .join(File, File.id == Chunk.file_id)
                .join(Project, Project.id == Chunk.project_id)
                .where(Project.embedding_fingerprint == fingerprint)
                .order_by(Chunk.id).offset(offset).limit(_PAGE)
            )).all()
            if not rows:
                return total
            actions = (
                {"_op_type": "update", "_index": index, "_id": str(cid),
                 "doc": {"path_text": path, "section": section}}
                for cid, section, path in rows
            )
            ok, _ = await async_bulk(es_client.es, actions, refresh="wait_for", raise_on_error=False, stats_only=True)
            total += ok
            offset += _PAGE


async def main() -> None:
    async with SessionLocal() as session:
        prints = (await session.scalars(
            select(Project.embedding_fingerprint).where(Project.embedding_fingerprint.is_not(None)).distinct()
        )).all()
    for fp in prints:
        dims = int(fp.rsplit(":", 1)[-1])
        print(f"{fp}: updated {await backfill(fp, dims)} docs")
    await es_client.es.close()


if __name__ == "__main__":
    asyncio.run(main())
