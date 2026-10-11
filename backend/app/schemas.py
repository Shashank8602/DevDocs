import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ProjectDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class ProjectCreate(BaseModel):
    name: ProjectName
    description: ProjectDescription | None = None


class ProjectUpdate(BaseModel):
    """Rename / edit. Only fields that are sent are changed; the id never changes."""

    name: ProjectName | None = None
    description: ProjectDescription | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: str
    file_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_ingested_at: datetime | None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_path: str
    file_name: str
    file_type: str
    language: str | None
    file_size: int
    status: str
    updated_at: datetime


class UploadResultItem(BaseModel):
    path: str
    status: str  # accepted | rejected
    reason: str | None = None


class UploadReport(BaseModel):
    accepted: int
    rejected: int
    results: list[UploadResultItem]
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    unchanged: list[str] = []


class FileReplaceResult(BaseModel):
    id: uuid.UUID
    changed: bool  # False when the content hash matched, so nothing was reprocessed


class LanguageCount(BaseModel):
    language: str | None
    count: int


class ProjectOverview(BaseModel):
    file_count: int
    by_type: dict[str, int]  # doc | source | other
    languages: list[LanguageCount]
    last_updated: datetime | None


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    content: str
    line_start: int | None
    line_end: int | None
    page_number: int | None
    section: str | None
    token_count: int


class ActiveIngestion(BaseModel):
    project_id: uuid.UUID
    project_name: str
    remaining: int  # files still pending or processing
    total: int


# --- dev-only: ES Inspector and Retrieval Playground (Phase 5) ---
class ESInspectorFile(BaseModel):
    file_id: uuid.UUID
    path: str
    status: str
    postgres_chunks: int
    es_chunks: int


class ESOrphan(BaseModel):
    file_id: str
    es_chunks: int


class ESTotals(BaseModel):
    postgres: int
    es: int


class ESInspectorOut(BaseModel):
    index: str
    totals: ESTotals
    files: list[ESInspectorFile]
    orphans: list[ESOrphan]


class ESCountOut(BaseModel):
    index: str
    project_id: uuid.UUID
    count: int


class PlaygroundHit(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    file_path: str
    score: float
    content: str
    line_start: int | None
    line_end: int | None
    section: str | None


class PlaygroundOut(BaseModel):
    index: str
    query: str
    ready_files: int
    bm25: list[PlaygroundHit]
    vector: list[PlaygroundHit]
    vector_error: str | None = None  # the embedding call failed; BM25 results are still returned


class PipelineHit(BaseModel):
    """One candidate chunk of Playground v2, with what happened to it."""
    rank: int  # position in serving order, 1 = first
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    file_path: str
    content: str
    line_start: int | None
    line_end: int | None
    page_number: int | None
    section: str | None
    token_count: int
    rrf_score: float
    bm25_rank: int | None
    vector_rank: int | None
    vector_score: float | None  # cosine score of the vector leg; None if only BM25 found the chunk
    rerank_score: float | None  # None unless the optional reranker scored this chunk
    kept: bool
    dropped_by: str | None  # gate | relative | threshold | overlap | top_k | budget


class PipelineOut(BaseModel):
    index: str
    query: str
    ready_files: int
    gate_threshold: float  # the refusal gate: top vector similarity must reach this
    top_vector_score: float | None
    gate_passed: bool
    relative_cutoff: float  # chunks more than this below the best vector similarity are dropped
    bm25_protect: int  # the best N BM25 hits are exempt from that filter
    reranked: bool  # True when the optional reranker was on for this query
    bm25: list[PlaygroundHit]
    vector: list[PlaygroundHit]
    fused: int  # how many distinct chunks RRF produced
    candidates: list[PipelineHit]  # every fused candidate in serving order, with what happened to it
    final: list[PipelineHit]
    context_tokens: int
    token_budget: int
    top_k: int
    no_relevant_context: bool
    error: str | None = None  # RetrievalError: search infrastructure failed after retries


class VectorPeekChunk(BaseModel):
    chunk_id: uuid.UUID
    ordinal: int
    line_start: int | None
    line_end: int | None
    page_number: int | None
    section: str | None
    token_count: int
    content_preview: str
    dims: int
    norm: float  # L2 length; truncated Gemini vectors are not unit length, hence cosine similarity
    min: float
    max: float
    preview: list[float]  # first values only
    values: list[float] | None = None  # all values, only when ?full=true


class VectorPeekOut(BaseModel):
    index: str
    file_path: str
    file_status: str
    postgres_chunks: int
    chunks: list[VectorPeekChunk]
