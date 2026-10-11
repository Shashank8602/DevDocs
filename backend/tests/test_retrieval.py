"""Phase 6: RRF fusion, selection (gate / relative filter / overlap / budget / Top-K), the pipeline against
real Postgres + ES with fake embeddings and a fake (optional) reranker, and the dev Playground v2 endpoint.

With the fake embeddings an on-topic question scores about 0.9 (ES cosine score) and an off-topic one about
0.5, so the production gate of 0.82 separates them."""
import re
import uuid

import pytest
from elastic_transport import ConnectionError as ESConnectionError

from app.config import settings
from app.providers.embeddings import EmbeddingError
from app.retrieval import pipeline
from app.retrieval.bm25 import Hit
from app.retrieval.fusion import rrf_fuse
from app.retrieval.reranker import RerankError, set_reranker
from app.retrieval.selection import Candidate, gate_passes, relative_filter, select

AUTH = "def verify_identity(token):\n    return validate_token(token)\n"
JWT = "def validate_token(token):\n    return decode(token)\n"
PAY = "def charge_card(amount):\n    return invoice(amount)\n"


class FakeReranker:
    """Offline: score = 2 x (query words found in the passage) - 5, so 3 shared words is relevant (> 0)
    and an unrelated passage lands at -5. Records what it was asked to score."""
    name = "fake"

    def __init__(self):
        self.calls: list[list[str]] = []

    async def score(self, query, passages):
        self.calls.append(passages)
        words = set(re.findall(r"\w+", query.lower()))
        return [2.0 * len(words & set(re.findall(r"\w+", p.lower()))) - 5.0 for p in passages]


@pytest.fixture(autouse=True)
def reranker(monkeypatch):
    fake = FakeReranker()
    set_reranker(fake)
    monkeypatch.setattr(settings, "retrieval_backoff_seconds", 0.0)
    yield fake
    set_reranker(None)


# --- pure stages ---
def _hit(i, score=1.0):
    return Hit(f"c{i}", f"f{i}", f"p{i}.py", score, f"content {i}")


def test_rrf_chunk_in_both_legs_outranks_chunks_in_one():
    fused = rrf_fuse([_hit(1), _hit(2)], [_hit(3), _hit(2)], k=60)
    assert [f.chunk_id for f in fused][0] == "c2"
    assert sorted(f.chunk_id for f in fused) == ["c1", "c2", "c3"]  # no duplicates
    top = fused[0]
    assert (top.bm25_rank, top.vector_rank) == (2, 2)
    assert top.rrf_score == pytest.approx(2 / 62)


def _cand(i, score, start=None, end=None, tokens=100, file="f"):
    return Candidate(
        f"c{i}", file, "a.py", "x", start, end, None, None, tokens, 0.0, None, None, rerank_score=score,
    )


def test_select_applies_threshold_overlap_budget_and_top_k_in_order():
    ranked = [
        _cand(1, 5.0, 1, 10), _cand(2, 4.0, 8, 20),  # overlaps c1 -> dropped
        _cand(3, 3.0, 30, 40), _cand(4, 2.0, 50, 60, tokens=2000),  # would blow the budget
        _cand(5, 1.0, 70, 80),  # rank order: nothing below a budget miss is added
        _cand(6, -1.0, 90, 99),  # below threshold
    ]
    kept = select(ranked, threshold=0.0, top_k=5, token_budget=500)
    assert [c.chunk_id for c in kept] == ["c1", "c3"]
    assert [c.dropped_by for c in ranked] == [None, "overlap", None, "budget", "budget", "threshold"]


def test_select_top_k_and_first_chunk_always_fits():
    many = [_cand(i, 9.0 - i, i * 100, i * 100 + 5) for i in range(8)]
    assert len(select(many, 0.0, top_k=5, token_budget=10_000)) == 5
    assert [c.dropped_by for c in many[5:]] == ["top_k"] * 3
    huge = [_cand(1, 1.0, tokens=9_000)]
    assert len(select(huge, 0.0, top_k=5, token_budget=3_000)) == 1


def test_select_does_not_treat_pages_or_other_files_as_overlapping():
    a, b = _cand(1, 2.0, None, None, file="f"), _cand(2, 1.0, None, None, file="f")  # page-based, no lines
    c, d = _cand(3, 2.0, 1, 10, file="f1"), _cand(4, 1.0, 1, 10, file="f2")  # same lines, different files
    assert len(select([a, b], 0.0, 5, 3000)) == 2
    assert len(select([c, d], 0.0, 5, 3000)) == 2


def _vc(i, vector_score, bm25_rank=None):
    c = _cand(i, 0.0)
    c.vector_score, c.bm25_rank, c.rerank_score = vector_score, bm25_rank, None
    return c


def test_gate_passes_only_when_the_best_vector_similarity_reaches_the_threshold():
    assert gate_passes(0.82, 0.82) and gate_passes(0.9, 0.82)
    assert not gate_passes(0.8199, 0.82)
    assert not gate_passes(None, 0.0)  # the vector leg found nothing: nothing to measure, so refuse


def test_relative_filter_keeps_chunks_near_the_best_and_marks_the_rest():
    ordered = [_vc(1, 0.90), _vc(2, 0.87), _vc(3, 0.85), _vc(4, None)]
    kept = relative_filter(ordered, best_vector_score=0.90, cutoff=0.04, bm25_protect=0)
    assert [c.chunk_id for c in kept] == ["c1", "c2"]  # 0.85 is 0.05 below the best; BM25-only has no score
    assert [c.dropped_by for c in ordered] == [None, None, "relative", "relative"]


def test_relative_filter_protects_the_best_bm25_hits():
    ordered = [_vc(1, 0.90), _vc(2, 0.70, bm25_rank=2), _vc(3, 0.70, bm25_rank=3), _vc(4, None, bm25_rank=1)]
    kept = relative_filter(ordered, 0.90, cutoff=0.04, bm25_protect=2)
    assert [c.chunk_id for c in kept] == ["c1", "c2", "c4"]  # bm25 rank 1 and 2 survive; rank 3 does not
    assert ordered[2].dropped_by == "relative"


def test_relative_filter_with_zero_cutoff_is_off_and_keeps_order():
    ordered = [_vc(1, 0.90), _vc(2, 0.10), _vc(3, None)]
    assert [c.chunk_id for c in relative_filter(ordered, 0.90, cutoff=0, bm25_protect=2)] == ["c1", "c2", "c3"]


def test_relative_filter_clears_a_stale_mark_when_a_chunk_is_kept_again():
    c = _vc(1, 0.90)
    c.dropped_by = "relative"
    assert relative_filter([c], 0.90, cutoff=0.04, bm25_protect=0) == [c] and c.dropped_by is None


def test_select_ignores_the_threshold_for_candidates_the_reranker_did_not_score():
    unscored = [_vc(1, 0.9), _vc(2, 0.9)]  # rerank_score is None: the optional reranker is off
    assert len(select(unscored, threshold=100.0, top_k=5, token_budget=3000)) == 2


# --- pipeline (Postgres + ES, fake embeddings and reranker) ---
async def _project(client, name):
    return (await client.post("/projects", json={"name": name})).json()["id"]


async def _ingested(client, ingest, items, name="Demo"):
    pid = await _project(client, name)
    files = [("files", (p, data.encode())) for p, data in items]
    res = await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})
    assert res.status_code == 200
    await ingest(pid)
    return pid


async def _retrieve(sessions, pid, q, **kw):
    async with sessions() as s:
        return await pipeline.retrieve(s, uuid.UUID(pid), q, **kw)


async def test_relevant_question_returns_the_matching_chunk_with_metadata(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("auth.py", AUTH), ("jwt.py", JWT), ("pay.py", PAY)])
    result = await _retrieve(sessions, pid, "validate_token decode token")
    assert [c.file_path for c in result][0] == "jwt.py"
    top = result[0]
    assert (top.line_start, top.line_end) == (1, 2) and top.token_count > 0
    assert top.vector_score > settings.gate_vector_threshold and top.rerank_score is None  # reranker is off


async def test_off_topic_question_returns_no_relevant_context(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("jwt.py", JWT), ("pay.py", PAY)])
    result = await _retrieve(sessions, pid, "best beaches in goa")
    assert isinstance(result, pipeline.NoRelevantContext) and result.reason.startswith("gate: top vector similarity")


async def test_project_with_no_ready_files_is_no_context_and_embeds_nothing(client, sessions, _search_backend):
    pid = await _project(client, "Empty")
    result = await _retrieve(sessions, pid, "validate_token")
    assert isinstance(result, pipeline.NoRelevantContext)
    assert _search_backend.calls == []  # nothing to search, so no embedding call (and no cost)


async def test_other_projects_chunks_are_never_returned(client, ingest, sessions):
    mine = await _ingested(client, ingest, [("pay.py", PAY)], name="Mine")
    await _ingested(client, ingest, [("jwt.py", JWT)], name="Other")
    result = await _retrieve(sessions, mine, "validate_token decode token")
    assert isinstance(result, pipeline.NoRelevantContext)  # jwt.py exists only in the other project

    trace = await _trace(sessions, mine, "validate_token decode token")
    assert {c.file_path for c in trace.candidates} <= {"pay.py"}


async def _trace(sessions, pid, q, **kw):
    async with sessions() as s:
        return await pipeline.run(s, uuid.UUID(pid), q, **kw)


async def test_files_that_are_not_ready_are_not_searched(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    async with sessions() as s:
        from sqlalchemy import update
        from app.models import File
        await s.execute(update(File).where(File.project_id == uuid.UUID(pid)).values(status="processing"))
        await s.commit()
    result = await _retrieve(sessions, pid, "validate_token decode token")
    assert isinstance(result, pipeline.NoRelevantContext)


async def test_overlapping_chunks_of_one_file_are_cleaned_up(client, ingest, sessions):
    long_file = "\n".join(f"def validate_token_{i}(token):\n    return decode(token, {i})\n" for i in range(400))
    pid = await _ingested(client, ingest, [("big.py", long_file)])
    trace = await _trace(sessions, pid, "validate_token decode token")
    assert len(trace.candidates) > 1  # several chunks of the same file were candidates
    kept = trace.final
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            assert a.line_end < b.line_start or b.line_end < a.line_start


async def test_gate_override_changes_the_decision(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    assert (await _trace(sessions, pid, "validate_token decode token", gate_threshold=0.0)).final
    refused = await _trace(sessions, pid, "validate_token decode token", gate_threshold=1.1)
    assert not refused.final and not refused.gate_passed
    assert [c.dropped_by for c in refused.candidates] == ["gate"]  # the Playground shows why nothing was served


async def test_the_reranker_is_off_by_default_and_never_called(client, ingest, sessions, reranker):
    pid = await _ingested(client, ingest, [("auth.py", AUTH), ("jwt.py", JWT)])
    trace = await _trace(sessions, pid, "validate_token decode token")
    assert trace.final and trace.reranked is False and reranker.calls == []
    assert all(c.rerank_score is None for c in trace.candidates)


async def test_gate_refusal_skips_the_reranker_even_when_it_is_on(client, ingest, sessions, reranker, monkeypatch):
    monkeypatch.setattr(settings, "rerank_top_n", 5)
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    result = await _retrieve(sessions, pid, "best beaches in goa")
    assert isinstance(result, pipeline.NoRelevantContext) and reranker.calls == []  # nothing to score, nothing paid


async def test_optional_reranker_scores_only_the_first_n_and_orders_them_by_score(
    client, ingest, sessions, reranker, monkeypatch,
):
    pid = await _ingested(client, ingest, [("auth.py", AUTH), ("jwt.py", JWT), ("pay.py", PAY)])
    trace = await _trace(sessions, pid, "validate_token decode token", rerank_top_n=2, relative_cutoff=0)
    assert trace.reranked is True and len(reranker.calls) == 1 and len(reranker.calls[0]) == 2
    scored = [c for c in trace.candidates if c.rerank_score is not None]
    assert len(scored) == 2 and scored == sorted(scored, key=lambda c: c.rerank_score, reverse=True)
    assert trace.candidates[:2] == scored  # scored head first, the rest follow in RRF order
    assert trace.final[0].file_path == "jwt.py"


async def test_relative_filter_drops_weaker_chunks_unless_bm25_protects_them(client, ingest, sessions, monkeypatch):
    pid = await _ingested(client, ingest, [("auth.py", AUTH), ("jwt.py", JWT), ("pay.py", PAY)])
    q = "validate_token decode token"
    # jwt.py is far more similar than auth.py (about 0.91 vs 0.81 with the fake embeddings)
    monkeypatch.setattr(settings, "bm25_protect", 0)
    strict = await _trace(sessions, pid, q, relative_cutoff=0.04)
    assert [c.file_path for c in strict.final] == ["jwt.py"]
    assert {c.dropped_by for c in strict.candidates if c.file_path == "auth.py"} == {"relative"}
    monkeypatch.setattr(settings, "bm25_protect", 2)  # auth.py is BM25 rank 2 (it contains validate_token)
    protected = await _trace(sessions, pid, q, relative_cutoff=0.04)
    assert [c.file_path for c in protected.final] == ["jwt.py", "auth.py"]
    off = await _trace(sessions, pid, q, relative_cutoff=0)
    assert {c.file_path for c in off.final} >= {"jwt.py", "auth.py"}


async def test_transient_search_errors_are_retried_then_succeed(client, ingest, sessions, monkeypatch):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    real, calls = pipeline.bm25_search, []

    async def flaky(*a, **kw):
        calls.append(1)
        if len(calls) < 3:  # fails twice, succeeds on the third (= 2 retries)
            raise ESConnectionError("es down")
        return await real(*a, **kw)

    monkeypatch.setattr(pipeline, "bm25_search", flaky)
    result = await _retrieve(sessions, pid, "validate_token decode token")
    assert len(calls) == 3 and result[0].file_path == "jwt.py"


async def test_exhausted_retries_raise_retrieval_error(client, ingest, sessions, monkeypatch):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    calls = []

    async def down(*a, **kw):
        calls.append(1)
        raise ESConnectionError("es down")

    monkeypatch.setattr(pipeline, "bm25_search", down)
    with pytest.raises(pipeline.RetrievalError):
        await _retrieve(sessions, pid, "validate_token")
    assert len(calls) == settings.retrieval_max_retries + 1


async def test_embedding_failure_becomes_retrieval_error(client, ingest, sessions, _search_backend, monkeypatch):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])

    async def boom(text):
        raise EmbeddingError("429")

    monkeypatch.setattr(_search_backend, "embed_query", boom)
    with pytest.raises(pipeline.RetrievalError):
        await _retrieve(sessions, pid, "validate_token")


async def test_rerank_failure_becomes_retrieval_error(client, ingest, sessions, monkeypatch):
    monkeypatch.setattr(settings, "rerank_top_n", 5)
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])

    class Broken:
        name = "broken"

        async def score(self, q, p):
            raise RerankError("model missing")

    set_reranker(Broken())  # the autouse fixture restores the default afterwards
    with pytest.raises(pipeline.RetrievalError):  # the question must pass the gate for the reranker to run
        await _retrieve(sessions, pid, "validate_token decode token")


# --- Playground v2 (dev endpoint) ---
async def test_playground_v2_shows_every_stage(client, ingest):
    pid = await _ingested(client, ingest, [("auth.py", AUTH), ("jwt.py", JWT), ("pay.py", PAY)])
    res = await client.get(f"/projects/{pid}/retrieval-pipeline", params={"q": "validate_token decode token"})
    assert res.status_code == 200
    body = res.json()
    assert body["ready_files"] == 3 and body["bm25"] and body["vector"] and body["fused"] >= 1
    assert body["candidates"][0]["file_path"] == "jwt.py" and body["candidates"][0]["kept"] is True
    assert [h["file_path"] for h in body["final"]][0] == "jwt.py"
    assert body["context_tokens"] == sum(h["token_count"] for h in body["final"]) and body["token_budget"] == 3000
    assert body["gate_passed"] is True and body["top_vector_score"] >= body["gate_threshold"] == 0.82
    assert body["relative_cutoff"] == 0.04 and body["bm25_protect"] == 2 and body["reranked"] is False
    assert body["candidates"][0]["vector_score"] > 0.82 and body["candidates"][0]["rerank_score"] is None
    assert body["no_relevant_context"] is False


async def test_playground_v2_off_topic_and_gate_override(client, ingest):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    off = (await client.get(f"/projects/{pid}/retrieval-pipeline", params={"q": "best beaches in goa"})).json()
    assert off["no_relevant_context"] is True and off["final"] == []
    assert off["gate_passed"] is False and {h["dropped_by"] for h in off["candidates"]} == {"gate"}
    forced = (await client.get(
        f"/projects/{pid}/retrieval-pipeline", params={"q": "best beaches in goa", "gate": 0, "cutoff": 0},
    )).json()
    assert forced["gate_threshold"] == 0 and forced["relative_cutoff"] == 0 and forced["final"]


async def test_playground_v2_is_hidden_without_dev_mode(client, ingest, monkeypatch):
    pid = await _ingested(client, ingest, [("jwt.py", JWT)])
    monkeypatch.setattr(settings, "dev_mode", False)
    assert (await client.get(f"/projects/{pid}/retrieval-pipeline", params={"q": "x"})).status_code == 404


# --- upload rules ---
async def test_lock_files_are_ignored_on_upload(client):
    pid = (await client.post("/projects", json={"name": "Locks"})).json()["id"]
    files = [("files", ("package-lock.json", b"{}")), ("files", ("package.json", b"{}"))]
    res = await client.post(f"/projects/{pid}/upload", files=files, data={"paths": ["app/package-lock.json", "app/package.json"]})
    by_path = {r["path"]: r for r in res.json()["results"]}
    assert by_path["app/package-lock.json"] == {"path": "app/package-lock.json", "status": "rejected", "reason": "ignored"}
    assert by_path["app/package.json"]["status"] == "accepted"
