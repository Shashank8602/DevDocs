"""Selection: the refusal gate, the relative precision filter, overlap cleanup, token budget and final Top-K.
Pure functions over `Candidate`s (already in serving order), so the eval can replay them at any setting."""
from dataclasses import dataclass


@dataclass
class Candidate:
    chunk_id: str
    file_id: str
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
    rerank_score: float | None = None  # only set when the optional reranker scored this candidate
    # None = kept in the final context; otherwise the stage that dropped it:
    # "gate" | "relative" | "threshold" | "overlap" | "budget" | "top_k"
    dropped_by: str | None = None
    vector_score: float | None = None  # cosine score from the vector leg; None if only BM25 found it


def gate_passes(top_vector_score: float | None, threshold: float) -> bool:
    """The refusal gate: answer only when the best vector similarity reaches `threshold`.
    No vector result at all means nothing to measure, so the question is refused."""
    return top_vector_score is not None and top_vector_score >= threshold


def relative_filter(
    ordered: list[Candidate], best_vector_score: float | None, cutoff: float, bm25_protect: int,
) -> list[Candidate]:
    """Keep a chunk when its vector similarity is within `cutoff` of the best one, or when it is one of the
    `bm25_protect` best BM25 hits (exact identifier / config-name matches embed poorly, so the vector
    similarity alone would cut them). Everything else gets `dropped_by="relative"`. Order is preserved.
    `cutoff <= 0` disables the filter."""
    if cutoff <= 0 or best_vector_score is None:
        return list(ordered)
    kept = []
    for c in ordered:
        near = c.vector_score is not None and c.vector_score >= best_vector_score - cutoff
        protected = c.bm25_rank is not None and c.bm25_rank <= bm25_protect
        if near or protected:
            c.dropped_by = None
            kept.append(c)
        else:
            c.dropped_by = "relative"
    return kept


def _overlaps(a: Candidate, b: Candidate) -> bool:
    if a.file_id != b.file_id or None in (a.line_start, a.line_end, b.line_start, b.line_end):
        return False  # only line-ranged chunks of the same file can overlap (pages / structured files never do)
    return a.line_start <= b.line_end and b.line_start <= a.line_end


def select(
    ranked: list[Candidate], threshold: float, top_k: int, token_budget: int,
) -> list[Candidate]:
    """Walk `ranked` (serving order), set `dropped_by` on every candidate and return the kept ones.

    1. rerank score below `threshold` -> "threshold"  (only candidates the optional reranker scored)
    2. overlaps a better kept chunk -> "overlap"   (the better-ranked chunk wins; ranges are never merged)
    3. final Top-K already full     -> "top_k"
    4. would exceed the token budget-> "budget"    (rank order is kept: nothing below it is added either)
    The best surviving chunk is always kept, even when it alone exceeds the budget."""
    kept: list[Candidate] = []
    used = 0
    budget_full = False
    for c in ranked:
        c.dropped_by = None
        if c.rerank_score is not None and c.rerank_score < threshold:
            c.dropped_by = "threshold"
        elif any(_overlaps(c, k) for k in kept):
            c.dropped_by = "overlap"
        elif len(kept) >= top_k:
            c.dropped_by = "top_k"
        elif budget_full or (kept and used + c.token_count > token_budget):
            budget_full = True
            c.dropped_by = "budget"
        else:
            kept.append(c)
            used += c.token_count
    return kept
