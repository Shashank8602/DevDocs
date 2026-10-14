"""Re-indexing (Phase 11): re-embed a project's stored chunks into the index of the current embedding
fingerprint, without re-parsing or re-chunking. Chunk ids are kept, so the Chunk Inspector shows the same
chunks before and after.

Order of effects, so a failure at any point leaves the project usable and the job repeatable:
  1. chunks are embedded and written to the NEW index; the old index keeps serving until step 2
  2. only when every chunk is in: the project's fingerprint is switched and its status derived again
  3. the project's docs are then removed from the old index
Chunks already present in the new index are skipped, so a run interrupted by an API quota error resumes
instead of paying for the same embeddings again. Embeddings go through the shared rate limiter."""
import asyncio
import logging
import uuid
from dataclasses import dataclass

from elasticsearch import NotFoundError
from elasticsearch.helpers import async_scan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import SessionLocal
from app.ingestion.status import refresh_project_status
from app.models import Chunk, File, Project
from app.providers.embeddings import get_embedding_provider
from app.search import es_client, indexer
from app.search.index_manager import ensure_index, index_name

log = logging.getLogger("devdocs.reindex")

SLICE = 50  # chunks per embedding request group; keeps progress granular and memory small


@dataclass
class Progress:
    state: str  # running | done | error
    done: int = 0
    total: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total, "message": self.message}


_progress: dict[uuid.UUID, Progress] = {}
_tasks: dict[uuid.UUID, asyncio.Task] = {}


class ReindexConflict(Exception):
    """The project cannot be re-indexed right now (already running, or files are still being processed)."""


def progress_of(project_id: uuid.UUID) -> dict:
    p = _progress.get(project_id)
    return p.to_dict() if p else {"state": "idle", "done": 0, "total": 0, "message": ""}


async def start(project_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    running = _tasks.get(project_id)
    if running is not None and not running.done():
        raise ReindexConflict("A re-index is already running for this project.")
    async with sessions() as session:
        busy = await session.scalar(
            select(File.id).where(File.project_id == project_id, File.status.in_(("pending", "processing"))).limit(1)
        )
    if busy is not None:
        raise ReindexConflict("Files are still being processed.")
    _progress[project_id] = Progress("running")
    _tasks[project_id] = asyncio.create_task(_run(project_id, sessions))


async def wait(project_id: uuid.UUID) -> None:
    """Wait for a running job (tests, shutdown)."""
    task = _tasks.get(project_id)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


async def _existing_ids(fingerprint: str, project_id: uuid.UUID) -> set[str]:
    ids: set[str] = set()
    try:
        async for hit in async_scan(
            es_client.es, index=index_name(fingerprint), _source=False,
            query={"query": {"term": {"project_id": str(project_id)}}},
        ):
            ids.add(hit["_id"])
    except NotFoundError:
        pass
    return ids


async def _run(project_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]) -> None:
    progress = _progress[project_id]
    try:
        provider = get_embedding_provider()
        async with sessions() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise RuntimeError("project no longer exists")
            old_fp = project.embedding_fingerprint
            rows = (await session.execute(
                select(Chunk.id, Chunk.file_id, Chunk.content, Chunk.section, File.file_path, File.file_type)
                .join(File, File.id == Chunk.file_id)
                .where(Chunk.project_id == project_id, File.status == "ready")
                .order_by(File.file_path, Chunk.ordinal)
            )).all()
        index = await ensure_index(provider.fingerprint, provider.dims)
        done_ids = await _existing_ids(provider.fingerprint, project_id)
        todo = [r for r in rows if str(r[0]) not in done_ids]
        progress.total, progress.done = len(rows), len(rows) - len(todo)

        for i in range(0, len(todo), SLICE):
            part = todo[i:i + SLICE]
            vectors = await provider.embed_documents([r[2] for r in part])
            await indexer.index_chunks(index, [
                indexer.IndexDoc(cid, project_id, fid, path, ftype, content, vec, section)
                for (cid, fid, content, section, path, ftype), vec in zip(part, vectors)
            ])
            progress.done += len(part)

        async with sessions() as session:  # every chunk is in the new index: switch over
            project = await session.get(Project, project_id)
            if project is not None:
                project.embedding_fingerprint = provider.fingerprint
                project.status = "ready"  # leave `needs_reindex`; the next line derives the real status
                await refresh_project_status(session, project_id)
                await session.commit()
        if old_fp and old_fp != provider.fingerprint:
            try:
                await indexer.delete_project_docs(old_fp, project_id)
            except Exception as exc:  # reconcile / the index lifecycle removes whatever is left
                log.warning("could not remove old-index docs of project %s: %r", project_id, exc)
        progress.state = "done"
        log.info("re-indexed project %s: %d chunks into %s", project_id, len(rows), provider.fingerprint)
    except Exception as exc:
        log.warning("re-index of project %s failed: %r", project_id, exc)
        progress.state = "error"
        progress.message = "Re-indexing didn't finish. Your files are unchanged; try again."
