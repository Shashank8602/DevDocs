"""Dev-only tools (DEV_MODE=true): ES Inspector, ES doc count, Retrieval Playground v1 and v2 (full pipeline).
Every route here answers 404 unless dev mode is on."""
import logging
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Chunk, File, Project
from app.providers.embeddings import EmbeddingError, get_embedding_provider
from app.retrieval import pipeline
from app.retrieval.bm25 import Hit, bm25_search
from app.retrieval.selection import Candidate
from app.retrieval.vector import vector_search
from app.schemas import (
    ESCountOut, ESInspectorFile, ESInspectorOut, ESOrphan, ESTotals, PipelineHit, PipelineOut, PlaygroundHit,
    PlaygroundOut, VectorPeekChunk, VectorPeekOut,
)
from app.search import indexer
from app.search.index_manager import index_name
from app.search.reconcile import find_orphans

log = logging.getLogger("devdocs.dev")


def _dev_only() -> None:
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


router = APIRouter(prefix="/projects/{project_id}", tags=["dev"], dependencies=[Depends(_dev_only)])


@router.get("/es-count", response_model=ESCountOut)
async def es_count(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """ES doc count for a project id. Deliberately does not require the project to exist in Postgres."""
    fingerprint = await pipeline.resolve_fingerprint(session, project_id)
    return ESCountOut(
        index=index_name(fingerprint), project_id=project_id, count=await indexer.count_docs(fingerprint, project_id),
    )


@router.get("/es-inspector", response_model=ESInspectorOut)
async def es_inspector(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    fingerprint = await pipeline.resolve_fingerprint(session, project_id)

    rows = (await session.execute(
        select(File.id, File.file_path, File.status, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.file_id == File.id)
        .where(File.project_id == project_id)
        .group_by(File.id).order_by(File.file_path)
    )).all()
    in_es = await indexer.count_by_file(fingerprint, project_id, size=len(rows) + 50)
    orphans = await find_orphans(session, fingerprint, project_id)

    files = [
        ESInspectorFile(file_id=i, path=p, status=s, postgres_chunks=n, es_chunks=in_es.get(str(i), 0))
        for i, p, s, n in rows
    ]
    return ESInspectorOut(
        index=index_name(fingerprint),
        totals=ESTotals(postgres=sum(f.postgres_chunks for f in files), es=await indexer.count_docs(fingerprint, project_id)),
        files=files,
        orphans=[ESOrphan(file_id=f, es_chunks=n) for f, n in sorted(orphans.items())],
    )


async def _playground_hits(session: AsyncSession, hits: list[Hit]) -> list[PlaygroundHit]:
    meta = {
        str(c.id): c for c in (await session.scalars(select(Chunk).where(Chunk.id.in_([uuid.UUID(h.chunk_id) for h in hits])))).all()
    } if hits else {}
    hits_out = []
    for i, h in enumerate(hits, start=1):
        chunk = meta.get(h.chunk_id)
        hits_out.append(PlaygroundHit(
            rank=i, chunk_id=h.chunk_id, file_id=h.file_id, file_path=h.file_path, score=h.score, content=h.content,
            line_start=chunk.line_start if chunk else None,
            line_end=chunk.line_end if chunk else None,
            section=chunk.section if chunk else None,
        ))
    return hits_out


@router.get("/retrieval-playground", response_model=PlaygroundOut)
async def retrieval_playground(
    project_id: uuid.UUID, q: str = Query(min_length=1, max_length=2000), k: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """BM25 Top-k and vector Top-k side by side. Both are filtered by project_id and READY file ids,
    and the READY ids always come from Postgres, never from the client."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    provider = get_embedding_provider()
    fingerprint = await pipeline.resolve_fingerprint(session, project_id)
    ready_ids = list((await session.scalars(
        select(File.id).where(File.project_id == project_id, File.status == "ready")
    )).all())

    bm25 = await bm25_search(fingerprint, project_id, ready_ids, q, k)
    vector: list[Hit] = []
    vector_error = None
    try:
        vector = await vector_search(fingerprint, project_id, ready_ids, await provider.embed_query(q), k)
    except EmbeddingError as exc:
        log.warning("playground query embedding failed: %r", exc)
        vector_error = "Couldn't embed the query, so there are no vector results."
    return PlaygroundOut(
        index=index_name(fingerprint), query=q, ready_files=len(ready_ids),
        bm25=await _playground_hits(session, bm25), vector=await _playground_hits(session, vector),
        vector_error=vector_error,
    )


def _pipeline_hit(rank: int, c: Candidate) -> PipelineHit:
    return PipelineHit(
        rank=rank, chunk_id=uuid.UUID(c.chunk_id), file_id=uuid.UUID(c.file_id), file_path=c.file_path,
        content=c.content, line_start=c.line_start, line_end=c.line_end, page_number=c.page_number,
        section=c.section, token_count=c.token_count, rrf_score=c.rrf_score, bm25_rank=c.bm25_rank,
        vector_rank=c.vector_rank, vector_score=c.vector_score, rerank_score=c.rerank_score,
        kept=c.dropped_by is None, dropped_by=c.dropped_by,
    )


@router.get("/retrieval-pipeline", response_model=PipelineOut)
async def retrieval_pipeline(
    project_id: uuid.UUID, q: str = Query(min_length=1, max_length=2000),
    gate: float | None = Query(None, description="override the vector-similarity gate threshold for this query"),
    cutoff: float | None = Query(None, ge=0, description="override the relative cut-off (0 = no filter)"),
    session: AsyncSession = Depends(get_session),
):
    """Playground v2: the whole retrieval pipeline with every stage visible
    (BM25 -> vector -> RRF -> gate -> relative filter -> final context and its token count)."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    error = None
    try:
        trace = await pipeline.run(session, project_id, q, gate_threshold=gate, relative_cutoff=cutoff)
    except pipeline.RetrievalError as exc:
        log.warning("playground pipeline failed: %r", exc)
        error = "Search failed. Check that Elasticsearch and the embedding key are available."
        trace = pipeline.RetrievalTrace(
            query=q, index=index_name(await pipeline.resolve_fingerprint(session, project_id)), ready_files=0,
            gate_threshold=settings.gate_vector_threshold if gate is None else gate,
            relative_cutoff=settings.relative_cutoff if cutoff is None else cutoff,
        )
    return PipelineOut(
        index=trace.index, query=q, ready_files=trace.ready_files, gate_threshold=trace.gate_threshold,
        top_vector_score=trace.top_vector_score, gate_passed=trace.gate_passed,
        relative_cutoff=trace.relative_cutoff, bm25_protect=settings.bm25_protect, reranked=trace.reranked,
        bm25=await _playground_hits(session, trace.bm25), vector=await _playground_hits(session, trace.vector),
        fused=len(trace.fused),
        candidates=[_pipeline_hit(i, c) for i, c in enumerate(trace.candidates, start=1)],
        final=[_pipeline_hit(i, c) for i, c in enumerate(trace.final, start=1)],
        context_tokens=trace.context_tokens, token_budget=settings.retrieval_context_tokens,
        top_k=settings.retrieval_final_top_k, no_relevant_context=not trace.final and error is None,
        error=error,
    )


PEEK_VALUES = 16


@router.get("/files/{file_id}/vectors", response_model=VectorPeekOut)
async def file_vectors(
    project_id: uuid.UUID, file_id: uuid.UUID, full: bool = False, session: AsyncSession = Depends(get_session),
):
    """Vector peek: the embeddings ES holds for one file, with their chunk metadata from Postgres.
    Chunks that are not indexed (yet) are simply absent. `full=true` also returns every value."""
    file = await session.get(File, file_id)
    if file is None or file.project_id != project_id:  # a file is only reachable through its own project
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    fingerprint = await pipeline.resolve_fingerprint(session, project_id)
    vectors = await indexer.get_vectors(fingerprint, project_id, file_id)
    chunks = (await session.scalars(select(Chunk).where(Chunk.file_id == file_id).order_by(Chunk.ordinal))).all()

    out = []
    for c in chunks:
        v = vectors.get(str(c.id))
        if v is None:
            continue
        out.append(VectorPeekChunk(
            chunk_id=c.id, ordinal=c.ordinal, line_start=c.line_start, line_end=c.line_end,
            page_number=c.page_number, section=c.section, token_count=c.token_count,
            content_preview=c.content[:160], dims=len(v), norm=math.sqrt(sum(x * x for x in v)),
            min=min(v), max=max(v), preview=v[:PEEK_VALUES], values=v if full else None,
        ))
    return VectorPeekOut(
        index=index_name(fingerprint), file_path=file.file_path, file_status=file.status,
        postgres_chunks=len(chunks), chunks=out,
    )
