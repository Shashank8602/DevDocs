"""Phase 5: ES indexing, project isolation, the READY filter, deletes, reconcile, the dev tools and the
Gemini embedding provider's batching / 429 retry. Runs against docker-compose Postgres + ES with a fake
embedder (no network)."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Chunk, File
from app.providers.embeddings import EmbeddingError, GeminiEmbeddingProvider
from app.providers.rate_limit import RateLimiter, batch_by_budget, get_limiter, reset_limiters
from app.search import indexer
from app.search.reconcile import reconcile

AUTH = (
    "def authenticate(user, password):\n"
    "    # verify the user's credentials and return their identity\n"
    "    return lookup(user, password)\n"
)
JWT = (
    "def validate_token(token):\n"
    "    # check the signature and expiry of the JWT\n"
    "    return decode(token)\n"
)
PAY = "def charge_card(order):\n    # take the payment and send an invoice\n    return gateway.charge(order)\n"


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "dev_mode", True)


async def _project(client, name="Demo"):
    return (await client.post("/projects", json={"name": name})).json()["id"]


async def _upload(client, pid, items):
    files = [("files", (p.rsplit("/", 1)[-1], data)) for p, data in items]
    return await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})


async def _files(client, pid):
    return {f["file_path"]: f for f in (await client.get(f"/projects/{pid}/files")).json()}


async def _ingested(client, ingest, items, name="Demo"):
    pid = await _project(client, name)
    await _upload(client, pid, items)
    await ingest(pid)
    return pid


async def _es_count(client, pid) -> int:
    return (await client.get(f"/projects/{pid}/es-count")).json()["count"]


async def _search(client, pid, q):
    res = await client.get(f"/projects/{pid}/retrieval-playground", params={"q": q})
    assert res.status_code == 200
    return res.json()


def _paths(hits):
    return [h["file_path"] for h in hits]


async def test_ready_file_has_matching_postgres_and_es_chunks(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode())])
    assert {f["status"] for f in (await _files(client, pid)).values()} == {"ready"}

    insp = (await client.get(f"/projects/{pid}/es-inspector")).json()
    assert insp["totals"] == {"postgres": 2, "es": 2} and insp["orphans"] == []
    assert [(f["path"], f["status"], f["postgres_chunks"], f["es_chunks"]) for f in insp["files"]] == [
        ("auth.py", "ready", 1, 1), ("jwt.py", "ready", 1, 1),
    ]
    project = (await client.get(f"/projects/{pid}")).json()
    assert insp["index"] == "test-devdocs-fake-hash-64" and project["status"] == "ready"
    async with sessions() as s:  # Postgres stays the source of truth for the project's fingerprint too
        from app.models import Project
        assert (await s.get(Project, uuid.UUID(pid))).embedding_fingerprint == "fake:hash:64"


async def test_bm25_ranks_the_file_with_the_identifier_first(client, ingest):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode()), ("pay.py", PAY.encode())])
    result = await _search(client, pid, "validate_token")
    assert _paths(result["bm25"])[0] == "jwt.py"
    assert "pay.py" not in _paths(result["bm25"])


async def test_analyzer_matches_identifier_parts_and_camel_case(client, ingest):
    src = b"class JwtService:\n    def refreshSession(self):\n        return 1\n"
    pid = await _ingested(client, ingest, [("svc.py", src), ("auth.py", AUTH.encode())])
    for q in ("JwtService", "jwtservice", "Jwt", "refreshSession", "refresh session"):
        assert _paths((await _search(client, pid, q))["bm25"])[:1] == ["svc.py"], q


async def test_vector_search_finds_the_file_even_when_wording_differs(client, ingest):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("pay.py", PAY.encode())])
    result = await _search(client, pid, "where do we verify the user's identity")
    assert _paths(result["vector"])[0] == "auth.py"


async def test_projects_never_see_each_others_chunks(client, ingest):
    a = await _ingested(client, ingest, [("auth.py", AUTH.encode())], "banking")
    b = await _ingested(client, ingest, [("login.py", AUTH.encode())], "shop")
    for pid, expected in ((a, "auth.py"), (b, "login.py")):
        result = await _search(client, pid, "authentication verify credentials")
        assert set(_paths(result["bm25"])) == {expected} and set(_paths(result["vector"])) == {expected}
    assert await _es_count(client, a) == 1 and await _es_count(client, b) == 1


async def test_files_that_are_not_ready_never_appear(client, ingest, sessions):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode())])
    files = await _files(client, pid)
    assert await _es_count(client, pid) == 2  # docs exist in ES ...

    async with sessions() as s:  # ... but jwt.py is being (re)processed
        (await s.get(File, uuid.UUID(files["jwt.py"]["id"]))).status = "processing"
        await s.commit()
    result = await _search(client, pid, "validate_token token signature expiry")
    assert "jwt.py" not in _paths(result["bm25"]) + _paths(result["vector"])
    assert result["ready_files"] == 1

    async with sessions() as s:
        for f in (await s.scalars(select(File))).all():
            f.status = "pending"
        await s.commit()
    result = await _search(client, pid, "authenticate")
    assert result["bm25"] == [] and result["vector"] == []


async def test_deleting_a_file_removes_only_its_es_docs(client, ingest):
    big = "\n\n".join(f"def f{i}():\n    return {i}\n" for i in range(3))
    pid = await _ingested(client, ingest, [("auth.py", big.encode()), ("jwt.py", JWT.encode())])
    other = await _ingested(client, ingest, [("keep.py", PAY.encode())], "other")
    files = await _files(client, pid)
    before = (await client.get(f"/projects/{pid}/es-inspector")).json()
    n_auth = next(f for f in before["files"] if f["path"] == "auth.py")["es_chunks"]
    assert n_auth >= 1 and before["totals"]["es"] == n_auth + 1

    assert (await client.delete(f"/files/{files['auth.py']['id']}")).status_code == 204
    after = (await client.get(f"/projects/{pid}/es-inspector")).json()
    assert [f["path"] for f in after["files"]] == ["jwt.py"]
    assert after["totals"] == {"postgres": 1, "es": 1} and after["orphans"] == []
    assert await _es_count(client, other) == 1
    assert "auth.py" not in _paths((await _search(client, pid, "f0 f1 f2"))["bm25"])


async def test_deleting_a_project_removes_its_es_docs(client, ingest):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode())])
    other = await _ingested(client, ingest, [("keep.py", PAY.encode())], "other")
    assert await _es_count(client, pid) == 2

    assert (await client.delete(f"/projects/{pid}")).status_code == 204
    assert await _es_count(client, pid) == 0  # the project row is gone; ES still answers
    assert await _es_count(client, other) == 1


async def test_reupload_that_drops_a_file_removes_its_docs(client, ingest):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode())])
    await _upload(client, pid, [("auth.py", AUTH.encode())])  # jwt.py is gone from the folder
    assert await _es_count(client, pid) == 1


async def test_modified_file_replaces_its_docs(client, ingest):
    pid = await _ingested(client, ingest, [("a.py", JWT.encode())])
    await _upload(client, pid, [("a.py", PAY.encode())])
    await ingest(pid)
    assert await _es_count(client, pid) == 1
    result = await _search(client, pid, "validate_token")
    assert result["bm25"] == []
    assert _paths((await _search(client, pid, "charge_card"))["bm25"]) == ["a.py"]


async def test_orphans_are_listed_and_reconcile_removes_them(client, ingest, sessions, _search_backend):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode())])
    ghost = uuid.uuid4()
    fingerprint = _search_backend.fingerprint
    from app.search.index_manager import ensure_index
    index = await ensure_index(fingerprint, _search_backend.dims)
    await indexer.index_chunks(index, [indexer.IndexDoc(
        uuid.uuid4(), uuid.UUID(pid), ghost, "gone.py", "source", "def validate_token(): pass",
        await _search_backend.embed_query("validate_token"),
    )])

    insp = (await client.get(f"/projects/{pid}/es-inspector")).json()
    assert insp["orphans"] == [{"file_id": str(ghost), "es_chunks": 1}]
    assert insp["totals"] == {"postgres": 1, "es": 2}
    assert "gone.py" not in _paths((await _search(client, pid, "validate_token"))["bm25"])  # never READY

    async with sessions() as s:
        assert await reconcile(s, fingerprint) == {str(ghost): 1}
    assert (await client.get(f"/projects/{pid}/es-inspector")).json()["orphans"] == []
    assert await _es_count(client, pid) == 1


async def test_es_failure_marks_the_file_failed_and_leaves_nothing_behind(client, ingest, monkeypatch, sessions):
    async def boom(*_a, **_k):
        raise RuntimeError("es is down")

    monkeypatch.setattr(indexer, "index_chunks", boom)
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode())])
    assert (await _files(client, pid))["auth.py"]["status"] == "failed"  # never `ready` without ES
    async with sessions() as s:
        assert (await s.scalars(select(Chunk))).all() == []
    assert await _es_count(client, pid) == 0


async def test_embedding_failure_marks_the_file_failed(client, ingest, _search_backend, monkeypatch):
    async def boom(texts):
        raise EmbeddingError("quota")

    monkeypatch.setattr(_search_backend, "embed_documents", boom)
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode())])
    assert (await _files(client, pid))["auth.py"]["status"] == "failed"


async def test_documents_are_embedded_in_one_call_per_file(client, ingest, _search_backend):
    await _ingested(client, ingest, [("auth.py", AUTH.encode()), ("jwt.py", JWT.encode())])
    assert _search_backend.calls == [("documents", 1), ("documents", 1)]


async def test_dev_tools_are_hidden_without_dev_mode(client, ingest, monkeypatch):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode())])
    monkeypatch.setattr(settings, "dev_mode", False)
    for path in ("es-inspector", "es-count", "retrieval-playground?q=x"):
        assert (await client.get(f"/projects/{pid}/{path}")).status_code == 404


async def test_playground_survives_an_embedding_failure(client, ingest, _search_backend, monkeypatch):
    pid = await _ingested(client, ingest, [("jwt.py", JWT.encode())])

    async def boom(text):
        raise EmbeddingError("429")

    monkeypatch.setattr(_search_backend, "embed_query", boom)
    result = await _search(client, pid, "validate_token")
    assert _paths(result["bm25"]) == ["jwt.py"] and result["vector"] == [] and result["vector_error"]


async def test_playground_hits_carry_line_ranges(client, ingest):
    pid = await _ingested(client, ingest, [("jwt.py", JWT.encode())])
    hit = (await _search(client, pid, "validate_token"))["bm25"][0]
    assert (hit["line_start"], hit["line_end"]) == (1, 3)


# --- Gemini provider: batching and 429 retry (mock transport, no network) ---
def _provider(handler, **kw):
    args = dict(api_key="k", model="m", dims=3, batch_size=2, max_retries=3, backoff=0.0)
    args.update(kw)
    return GeminiEmbeddingProvider(transport=httpx.MockTransport(handler), **args)


def _ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2, 0.3]} for _ in body["requests"]]})


async def test_provider_batches_and_sets_task_type_and_dims():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["requests"])
        return _ok(request)

    vectors = await _provider(handler).embed_documents(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5 and [len(b) for b in seen] == [2, 2, 1]
    assert {r["taskType"] for b in seen for r in b} == {"RETRIEVAL_DOCUMENT"}
    assert {r["outputDimensionality"] for b in seen for r in b} == {3}

    seen.clear()
    await _provider(handler).embed_query("q")
    assert seen[0][0]["taskType"] == "RETRIEVAL_QUERY"


async def test_provider_retries_on_429_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429, json={"error": {"message": "rate limited"}}) if len(attempts) < 3 else _ok(request)

    assert len(await _provider(handler).embed_documents(["a"])) == 1
    assert len(attempts) == 3


async def test_provider_gives_up_after_max_retries_and_does_not_retry_client_errors():
    attempts = []

    def always_429(request):
        attempts.append(1)
        return httpx.Response(429, json={})

    with pytest.raises(EmbeddingError):
        await _provider(always_429, max_retries=2).embed_documents(["a"])
    assert len(attempts) == 3  # first try + 2 retries

    attempts.clear()
    with pytest.raises(EmbeddingError):
        await _provider(lambda r: (attempts.append(1), httpx.Response(400, json={}))[1]).embed_documents(["a"])
    assert len(attempts) == 1


async def test_provider_rejects_wrong_dimensions_and_missing_key():
    def wrong(request):
        return httpx.Response(200, json={"embeddings": [{"values": [0.1]}]})

    with pytest.raises(EmbeddingError):
        await _provider(wrong).embed_documents(["a"])
    with pytest.raises(EmbeddingError):
        await _provider(_ok, api_key="").embed_query("q")


# --- client-side rate limiter (fake clock: no real waiting) ---
class _Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


async def test_limiter_lets_calls_through_until_full_then_waits_for_the_window():
    from app.providers.rate_limit import SlidingWindowLimiter

    clock = _Clock()
    limiter = SlidingWindowLimiter(10, 60.0, clock, clock.sleep)
    await limiter.acquire(6)
    clock.now += 20
    await limiter.acquire(4)
    assert clock.slept == []  # exactly at the limit: no wait
    await limiter.acquire(1)  # must wait for the first 6 (taken 20s ago) to leave the window
    assert clock.slept == [40.0]


async def test_limiter_disabled_and_oversized_cost_do_not_block():
    from app.providers.rate_limit import SlidingWindowLimiter

    clock = _Clock()
    await SlidingWindowLimiter(0, 60.0, clock, clock.sleep).acquire(10**9)
    big = SlidingWindowLimiter(5, 60.0, clock, clock.sleep)
    await big.acquire(100)  # clamped to the limit, still goes through alone
    assert clock.slept == []


async def test_provider_paces_batches_under_the_text_limit_without_any_429():
    clock = _Clock()
    calls = []

    def handler(request):
        calls.append(clock.now)
        return _ok(request)

    limiter = RateLimiter(requests_per_minute=4, clock=clock, sleep=clock.sleep)
    provider = _provider(handler, batch_size=2, limiter=limiter)
    vectors = await provider.embed_documents([f"t{i}" for i in range(6)])  # 3 batches of 2 = 6 texts
    assert len(vectors) == 6
    assert calls[0] == calls[1] == 1000.0  # first 4 texts fit in the window
    assert calls[2] == 1060.0  # the third batch waited for the window to roll


async def test_provider_splits_batches_to_fit_the_token_budget():
    seen = []

    def handler(request):
        seen.append(len(json.loads(request.content)["requests"]))
        return _ok(request)

    clock = _Clock()
    text = "x" * 400  # ~100 tokens each
    limiter = RateLimiter(tokens_per_minute=1000, clock=clock, sleep=clock.sleep)  # a batch may use 25%: 250
    provider = _provider(handler, batch_size=50, limiter=limiter)
    await provider.embed_documents([text] * 5)
    assert seen == [2, 2, 1]  # never more than 250 tokens in one request


# --- the limiter is provider-agnostic: any key, limits from settings, shared per key ---
def test_get_limiter_reads_per_provider_limits_and_is_shared(monkeypatch):
    reset_limiters()
    monkeypatch.setattr(settings, "groq_llm_tpm", 8000, raising=False)
    monkeypatch.setattr(settings, "groq_llm_rpm", 30, raising=False)
    groq = get_limiter("groq_llm")
    assert (groq.requests_per_minute, groq.tokens_per_minute, groq.max_batch_tokens) == (30, 8000, 2000)
    assert get_limiter("groq_llm") is groq  # one window per key, process-wide
    unknown = get_limiter("some_future_provider")  # never configured: unlimited, never blocks
    assert (unknown.requests_per_minute, unknown.tokens_per_minute, unknown.max_batch_tokens) == (0, 0, 0)
    reset_limiters()


def test_batch_by_budget_respects_item_and_token_caps():
    assert batch_by_budget([], 5) == []
    assert [len(b) for b in batch_by_budget(["a"] * 5, 2)] == [2, 2, 1]
    big = "x" * 4000  # ~1000 tokens
    assert [len(b) for b in batch_by_budget([big] * 3, 10, max_tokens=500)] == [1, 1, 1]  # oversize goes alone
    assert [len(b) for b in batch_by_budget(["x" * 400] * 5, 10, max_tokens=250)] == [2, 2, 1]


async def test_vector_peek_returns_the_stored_vectors_for_one_file(client, ingest):
    big = "\n\n".join(f"def f{i}():\n    return {i}\n" for i in range(3))
    pid = await _ingested(client, ingest, [("auth.py", big.encode()), ("jwt.py", JWT.encode())])
    files = await _files(client, pid)

    res = await client.get(f"/projects/{pid}/files/{files['auth.py']['id']}/vectors")
    assert res.status_code == 200
    body = res.json()
    assert body["file_path"] == "auth.py" and len(body["chunks"]) == body["postgres_chunks"] >= 1
    first = body["chunks"][0]
    assert first["dims"] == 64 and len(first["preview"]) == 16 and first["values"] is None
    assert abs(first["norm"] - 1.0) < 1e-6  # the fake embedder returns unit vectors
    assert first["min"] <= first["max"] and first["content_preview"]

    full = (await client.get(f"/projects/{pid}/files/{files['auth.py']['id']}/vectors", params={"full": "true"})).json()
    assert len(full["chunks"][0]["values"]) == 64
    assert full["chunks"][0]["values"][:16] == full["chunks"][0]["preview"]


async def test_vector_peek_is_dev_only_and_project_scoped(client, ingest, monkeypatch):
    pid = await _ingested(client, ingest, [("auth.py", AUTH.encode())])
    other = await _project(client, "other")
    fid = (await _files(client, pid))["auth.py"]["id"]

    assert (await client.get(f"/projects/{other}/files/{fid}/vectors")).status_code == 404  # wrong project
    monkeypatch.setattr(settings, "dev_mode", False)
    assert (await client.get(f"/projects/{pid}/files/{fid}/vectors")).status_code == 404
