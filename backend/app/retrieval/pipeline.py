"""Hybrid retrieval: filter -> BM25 + kNN -> RRF -> vector-similarity gate -> (optional rerank) -> relative
filter -> overlap cleanup -> budget / Top-K.

`run` returns the full trace of every stage (the dev Retrieval Playground v2 shows it); `retrieve` is what
chat calls and returns the final chunks, or `NoRelevantContext` (the answer LLM must then not be called).
Isolation: the project filter sits inside both ES queries, and the READY file ids always come from Postgres."""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from elastic_transport import TransportError
from elasticsearch import ApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Chunk, File, Project
from app.providers.embeddings import EmbeddingError, get_embedding_provider
from app.retrieval.bm25 import Hit, bm25_search
from app.retrieval.fusion import FusedHit, rrf_fuse
from app.retrieval.reranker import RerankError, Reranker, get_reranker
from app.retrieval.selection import Candidate, gate_passes, relative_filter, select as select_context
from app.retrieval.vector import vector_search
from app.search.index_manager import index_name

log = logging.getLogger("devdocs.retrieval")

_RETRYABLE_STATUS = {429, 502, 503, 504}


class RetrievalError(Exception):
    """Search infrastructure failed after limited retries. The message is internal."""


@dataclass
class NoRelevantContext:
    """Nothing relevant survived the pipeline. Chat answers with the fallback and skips the LLM."""
    reason: str = "no relevant chunks"


@dataclass
class RetrievalTrace:
    query: str
    index: str
    ready_files: int
    gate_threshold: float
    relative_cutoff: float
    bm25: list[Hit] = field(default_factory=list)
    vector: list[Hit] = field(default_factory=list)
    fused: list[FusedHit] = field(default_factory=list)
    top_vector_score: float | None = None  # the gate's input: best cosine score of the vector leg
    gate_passed: bool = False
    reranked: bool = False  # True when the optional reranker scored the head of the candidates
    candidates: list[Candidate] = field(default_factory=list)  # serving order, each with `dropped_by`
    final: list[Candidate] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.ready_files:
            return "no ready files"
        if not self.gate_passed:
            top = "none" if self.top_vector_score is None else f"{self.top_vector_score:.3f}"
            return f"gate: top vector similarity {top} < {self.gate_threshold}"
        return "no relevant chunks"

    @property
    def context_tokens(self) -> int:
        return sum(c.token_count for c in self.final)


async def resolve_fingerprint(session: AsyncSession, project_id: uuid.UUID) -> str:
    """The index a project's docs live in: its stored fingerprint, else the configured one."""
    stored = await session.scalar(select(Project.embedding_fingerprint).where(Project.id == project_id))
    return stored or get_embedding_provider().fingerprint


async def _with_retries(call, what: str):
    """Limited retries for search infrastructure errors (connection problems, 429, 5xx); nothing else."""
    for attempt in range(settings.retrieval_max_retries + 1):
        try:
            return await call()
        except (TransportError, ApiError) as exc:
            retryable = isinstance(exc, TransportError) or exc.status_code in _RETRYABLE_STATUS
            if not retryable or attempt == settings.retrieval_max_retries:
                raise RetrievalError(f"{what} failed: {exc!r}") from exc
            wait = settings.retrieval_backoff_seconds * 2 ** attempt
            log.warning("%s failed (%r), retrying in %.2fs (%d/%d)", what, exc, wait, attempt + 1, settings.retrieval_max_retries)
            await asyncio.sleep(wait)
    raise RetrievalError(f"{what} failed")  # pragma: no cover


async def run(
    session: AsyncSession, project_id: uuid.UUID, query: str,
    *, gate_threshold: float | None = None, relative_cutoff: float | None = None,
    rerank_top_n: int | None = None, reranker: Reranker | None = None,
) -> RetrievalTrace:
    """Every keyword defaults to its setting; the eval and the Playground override them per call."""
    gate_threshold = settings.gate_vector_threshold if gate_threshold is None else gate_threshold
    relative_cutoff = settings.relative_cutoff if relative_cutoff is None else relative_cutoff
    rerank_top_n = settings.rerank_top_n if rerank_top_n is None else rerank_top_n
    fingerprint = await resolve_fingerprint(session, project_id)
    trace = RetrievalTrace(
        query=query, index=index_name(fingerprint), ready_files=0,
        gate_threshold=gate_threshold, relative_cutoff=relative_cutoff,
    )

    # 1. Eligible set: this project's READY files, from Postgres (never from the client).
    ready_ids = list((await session.scalars(
        select(File.id).where(File.project_id == project_id, File.status == "ready")
    )).all())
    trace.ready_files = len(ready_ids)
    if not ready_ids or not query.strip():
        return trace

    # 2. BM25 and embed + kNN, concurrently.
    async def bm25_leg() -> list[Hit]:
        return await _with_retries(
            lambda: bm25_search(fingerprint, project_id, ready_ids, query, settings.retrieval_leg_k), "BM25 search",
        )

    async def vector_leg() -> list[Hit]:
        try:
            vec = await get_embedding_provider().embed_query(query)  # the provider retries 429/5xx itself
        except EmbeddingError as exc:
            raise RetrievalError(f"query embedding failed: {exc}") from exc
        return await _with_retries(
            lambda: vector_search(fingerprint, project_id, ready_ids, vec, settings.retrieval_leg_k), "vector search",
        )

    trace.bm25, trace.vector = await asyncio.gather(bm25_leg(), vector_leg())

    # 3. RRF (k=60) in application code.
    trace.fused = rrf_fuse(trace.bm25, trace.vector, settings.rrf_k)
    if not trace.fused:
        return trace

    # Chunk metadata from Postgres. Joining on READY files also intersects ES results with Postgres,
    # so a stray ES doc (e.g. an orphan) can never reach the context.
    rows = {
        str(c.id): c for c in (await session.scalars(
            select(Chunk).join(File, File.id == Chunk.file_id).where(
                Chunk.id.in_([uuid.UUID(f.chunk_id) for f in trace.fused]),
                Chunk.project_id == project_id, File.status == "ready",
            )
        )).all()
    }
    candidates = [
        Candidate(
            f.chunk_id, f.file_id, f.file_path, rows[f.chunk_id].content,
            rows[f.chunk_id].line_start, rows[f.chunk_id].line_end, rows[f.chunk_id].page_number,
            rows[f.chunk_id].section, rows[f.chunk_id].token_count, f.rrf_score, f.bm25_rank, f.vector_rank,
            vector_score=f.vector_score,
        )
        for f in trace.fused if f.chunk_id in rows
    ]
    trace.candidates = candidates  # RRF order
    if not candidates:
        return trace

    # 4. Refusal gate on the best vector similarity (a free by-product of the kNN search).
    trace.top_vector_score = max((h.score for h in trace.vector), default=None)
    trace.gate_passed = gate_passes(trace.top_vector_score, gate_threshold)
    if not trace.gate_passed:
        for c in candidates:
            c.dropped_by = "gate"
        return trace

    # 5. Optional reranker: score the first N candidates and serve them first, best score first.
    #    The path is part of the passage so "what does auth.py do" can match on the file name.
    if rerank_top_n > 0:
        head = candidates[:rerank_top_n]
        try:
            scores = await (reranker or get_reranker()).score(query, [f"{c.file_path}\n{c.content}" for c in head])
        except RerankError as exc:
            raise RetrievalError(str(exc)) from exc
        for c, sc in zip(head, scores):
            c.rerank_score = sc
        trace.reranked = True
        trace.candidates = sorted(head, key=lambda c: c.rerank_score, reverse=True) + candidates[rerank_top_n:]

    # 6. Relative filter -> overlap cleanup -> token budget and final Top-K.
    survivors = relative_filter(trace.candidates, trace.top_vector_score, relative_cutoff, settings.bm25_protect)
    trace.final = select_context(
        survivors, float("-inf"), settings.retrieval_final_top_k, settings.retrieval_context_tokens,
    )
    return trace


async def retrieve(
    session: AsyncSession, project_id: uuid.UUID, query: str, *, reranker: Reranker | None = None,
) -> list[Candidate] | NoRelevantContext:
    trace = await run(session, project_id, query, reranker=reranker)
    if not trace.final:
        log.info("no relevant context for project %s (%d candidates): %s",
                 project_id, len(trace.candidates), trace.reason)
        return NoRelevantContext(trace.reason)
    return trace.final
