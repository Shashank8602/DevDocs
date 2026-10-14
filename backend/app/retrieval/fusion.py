"""Reciprocal Rank Fusion of the BM25 and vector rankings. Pure, done in application code (not ES)."""
import re
from dataclasses import dataclass

from app.retrieval.bm25 import Hit


@dataclass
class FusedHit:
    chunk_id: str
    file_id: str
    file_path: str
    content: str
    rrf_score: float
    bm25_rank: int | None  # 1-based position in the BM25 list, None if that leg did not return it
    vector_rank: int | None
    vector_score: float | None = None  # Elasticsearch cosine score, (1 + cosine) / 2; None if the vector leg missed it


def rrf_fuse(bm25: list[Hit], vector: list[Hit], k: int = 60) -> list[FusedHit]:
    """score(chunk) = sum over lists of 1 / (k + rank). One entry per chunk; ties keep BM25-first order."""
    fused: dict[str, FusedHit] = {}
    for leg, hits in (("bm25", bm25), ("vector", vector)):
        for rank, h in enumerate(hits, start=1):
            f = fused.get(h.chunk_id)
            if f is None:
                f = fused[h.chunk_id] = FusedHit(h.chunk_id, h.file_id, h.file_path, h.content, 0.0, None, None)
            f.rrf_score += 1.0 / (k + rank)
            if leg == "bm25":
                f.bm25_rank = rank
            else:
                f.vector_rank = rank
                f.vector_score = h.score
    return sorted(fused.values(), key=lambda f: f.rrf_score, reverse=True)  # sorted() is stable


# Test files repeat the vocabulary of the code they test, so they match implementation questions about as
# well as the implementation does, and crowd it out of a Top-5. Measured on 428 questions (eval.bm25_variants):
# demoting them by 20% lifted MRR 0.80 -> 0.83 (tuning) and 0.72 -> 0.79 (held-out) and the share of served
# chunks from a relevant file by +2.5 / +5 points, with hit rate equal or higher. 0.9 captured only part of it.
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?|fixtures)/|(^|/)test_[^/]+\.py$|\.(test|spec)\.[jt]sx?$", re.I,
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def apply_test_prior(fused: list[FusedHit], question: str, factor: float) -> list[FusedHit]:
    """Multiply the RRF score of test-file chunks by `factor` (<1 demotes) unless the question is about tests.
    Returns the list re-sorted (stable). `factor >= 1` is a no-op."""
    if factor >= 1 or "test" in question.lower():
        return fused
    for f in fused:
        if is_test_path(f.file_path):
            f.rrf_score *= factor
    return sorted(fused, key=lambda f: f.rrf_score, reverse=True)
