"""In-process background ingestion: one asyncio consumer, one job per project.

A job walks the project's pending files one at a time (pending -> processing -> ready | failed),
so one bad file never fails the project. It keeps running when the user leaves the page.

Write order per file: chunks go to Postgres first, then they are embedded and bulk-indexed in
Elasticsearch, and only then is the file marked `ready`."""
import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.ingestion.chunking import chunk_document
from app.ingestion.parsers import parse
from app.ingestion.status import refresh_project_status
from app.models import Chunk, File, Project
from app.providers.embeddings import get_embedding_provider
from app.search import indexer
from app.search.index_manager import ensure_index

log = logging.getLogger("devdocs.worker")

_queue: asyncio.Queue[uuid.UUID] | None = None
_queued: set[uuid.UUID] = set()
_consumer: asyncio.Task | None = None


def enqueue(project_id: uuid.UUID) -> None:
    """Schedule ingestion of the project's pending files. Safe to call repeatedly."""
    global _queue, _consumer
    if _queue is None:
        _queue = asyncio.Queue()
    if _consumer is None or _consumer.done():
        _consumer = asyncio.create_task(_consume(_queue))
    if project_id not in _queued:
        _queued.add(project_id)
        _queue.put_nowait(project_id)


async def _consume(queue: asyncio.Queue[uuid.UUID]) -> None:
    while True:
        project_id = await queue.get()
        _queued.discard(project_id)
        try:
            await process_project(project_id)
        except Exception:  # the consumer must outlive any single job
            log.exception("ingestion job failed for project %s", project_id)


async def start() -> None:
    """Called at app startup: files left `processing` by a crash are re-queued."""
    async with SessionLocal() as session:
        await session.execute(update(File).where(File.status == "processing").values(status="pending"))
        await session.commit()
        project_ids = (await session.scalars(select(File.project_id).where(File.status == "pending").distinct())).all()
    for project_id in project_ids:
        enqueue(project_id)


async def stop() -> None:
    global _consumer, _queue
    if _consumer is not None:
        _consumer.cancel()
        await asyncio.gather(_consumer, return_exceptions=True)
    _consumer, _queue = None, None
    _queued.clear()


async def process_project(project_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """Ingest every pending file of the project, then settle the project status."""
    while True:
        async with sessions() as session:
            row = await session.scalar(
                select(File).where(File.project_id == project_id, File.status == "pending")
                .order_by(File.file_path).limit(1)
            )
            if row is None:
                await refresh_project_status(session, project_id)
                await session.commit()
                return
            row.status = "processing"
            await refresh_project_status(session, project_id)
            await session.commit()
            job = (row.id, row.file_path, row.file_type, row.language)
        await _ingest_file(sessions, project_id, *job)


def _read_and_chunk(path: Path, file_path: str, file_type: str, language: str | None):
    return chunk_document(parse(path.read_bytes(), file_path, file_type, language))


async def _ingest_file(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID,
    file_id: uuid.UUID, file_path: str, file_type: str, language: str | None,
) -> None:
    provider = get_embedding_provider()
    error: str | None = None
    chunks = []
    try:
        path = settings.data_dir / "projects" / str(project_id) / file_path
        chunks = await asyncio.to_thread(_read_and_chunk, path, file_path, file_type, language)
    except Exception as exc:  # any parse problem fails this file only
        log.warning("could not process %s: %r", file_path, exc)
        error = repr(exc)

    # 1. Postgres: replace the file's chunks (the file stays `processing`).
    chunk_ids: list[uuid.UUID] = []
    if error is None:
        try:
            async with sessions() as session:
                row = await session.get(File, file_id)
                if row is None or row.status != "processing":
                    return  # deleted or replaced while it was being processed
                await session.execute(delete(Chunk).where(Chunk.file_id == file_id))
                chunk_ids = [uuid.uuid4() for _ in chunks]
                session.add_all(
                    Chunk(
                        id=cid, project_id=project_id, file_id=file_id, ordinal=c.ordinal, content=c.content,
                        line_start=c.line_start, line_end=c.line_end, page_number=c.page_number,
                        section=c.section, token_count=c.token_count,
                    )
                    for cid, c in zip(chunk_ids, chunks)
                )
                await session.commit()
        except Exception as exc:
            log.warning("could not store chunks of %s: %r", file_path, exc)
            error = repr(exc)

    # 2. Elasticsearch: embed, then replace the file's docs. Never `ready` without this.
    if error is None:
        try:
            await _index_file(provider, project_id, file_id, file_path, file_type, chunks, chunk_ids)
        except Exception as exc:
            log.warning("could not index %s: %r", file_path, exc)
            error = repr(exc)

    # 3. Settle the file: `ready`, or `failed` with no leftover chunks anywhere.
    for attempt in (1, 2):
        try:
            async with sessions() as session:
                row = await session.get(File, file_id)
                if row is None or row.status != "processing":
                    if row is None:  # deleted mid-flight: don't leave the docs we just wrote
                        await _drop_docs(provider.fingerprint, file_id)
                    return
                if error:
                    await session.execute(delete(Chunk).where(Chunk.file_id == file_id))
                    await _drop_docs(provider.fingerprint, file_id)
                    row.status, row.error_message = "failed", error
                else:
                    row.status, row.error_message = "ready", None
                    project = await session.get(Project, project_id)
                    if project is not None:
                        project.embedding_fingerprint = provider.fingerprint
                await refresh_project_status(session, project_id)
                await session.commit()
                return
        except Exception as exc:
            if attempt == 2:
                raise
            log.warning("could not settle %s: %r", file_path, exc)
            error = repr(exc)  # second attempt records the file as failed


async def _index_file(provider, project_id, file_id, file_path, file_type, chunks, chunk_ids) -> None:
    index = await ensure_index(provider.fingerprint, provider.dims)
    await indexer.delete_file_docs(provider.fingerprint, file_id)  # stale docs of a modified / retried file
    if not chunks:
        return
    vectors = await provider.embed_documents([c.content for c in chunks])
    await indexer.index_chunks(index, [
        indexer.IndexDoc(cid, project_id, file_id, file_path, file_type, c.content, vec)
        for cid, c, vec in zip(chunk_ids, chunks, vectors)
    ])


async def _drop_docs(fingerprint: str, file_id: uuid.UUID) -> None:
    try:
        await indexer.delete_file_docs(fingerprint, file_id)
    except Exception as exc:  # reconcile removes whatever is left
        log.warning("could not remove ES docs of file %s: %r", file_id, exc)
