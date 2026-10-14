"""Phase 11: settings (masked, encrypted keys, applied from the next query) and re-indexing."""
import json

import pytest
from sqlalchemy import select

from app import settings_store
from app.config import settings
from app.ingestion import reindex
from app.models import AppSettings, Chunk, Project
from app.providers.embeddings import fingerprint_of, set_embedding_provider
from app.providers.llm import get_llm_provider
from app.search import indexer
from tests.conftest import FakeEmbeddingProvider

JWT = "def validate_token(token):\n    return decode(token)\n"
PAY = "def charge_card(amount):\n    return invoice(amount)\n"


async def _project(client, ingest):
    pid = (await client.post("/projects", json={"name": "Demo"})).json()["id"]
    items = [("jwt.py", JWT), ("pay.py", PAY)]
    await client.post(
        f"/projects/{pid}/upload", files=[("files", (p, d.encode())) for p, d in items],
        data={"paths": [p for p, _ in items]},
    )
    await ingest(pid)
    return pid


# --- read / save ---
async def test_read_returns_values_defaults_and_masked_keys(client):
    body = (await client.get("/settings")).json()
    assert body["values"]["llm_provider"] == "groq"
    assert body["defaults"]["gate_vector_threshold"] == 0.82
    assert set(body["secrets"]) == {"groq_api_key", "gemini_api_key", "embedding_api_key", "jev_api_key"}
    assert "api_key" not in json.dumps(body["values"])


async def test_api_keys_are_encrypted_at_rest_and_never_returned_in_clear(client, sessions):
    res = await client.put("/settings", json={"groq_api_key": "gsk_supersecretvalue1234"})
    assert res.status_code == 200
    assert res.json()["secrets"]["groq_api_key"] == {"set": True, "masked": "••••1234"}
    assert "supersecret" not in res.text
    async with sessions() as s:
        stored = (await s.get(AppSettings, 1)).data["groq_api_key"]
    assert stored.startswith("enc:") and "supersecret" not in stored
    assert settings.groq_api_key == "gsk_supersecretvalue1234"  # applied to the running process
    assert (await client.get("/settings")).json()["secrets"]["groq_api_key"]["masked"] == "••••1234"


async def test_a_blank_key_keeps_the_stored_one(client):
    await client.put("/settings", json={"gemini_api_key": "AIza-original-9999"})
    await client.put("/settings", json={"gemini_api_key": "   ", "llm_temperature": 0.5})
    assert settings.gemini_api_key == "AIza-original-9999" and settings.llm_temperature == 0.5


async def test_stored_values_win_over_env_after_a_restart(client, sessions):
    await client.put("/settings", json={"llm_temperature": 0.7, "groq_api_key": "gsk_persisted_5678"})
    settings.llm_temperature, settings.groq_api_key = 0.2, ""  # a fresh process: .env values
    async with sessions() as s:
        await settings_store.load_overrides(s)
    assert settings.llm_temperature == 0.7 and settings.groq_api_key == "gsk_persisted_5678"


@pytest.mark.parametrize("patch", [
    {"llm_temperature": 5}, {"llm_provider": "openai"}, {"gate_vector_threshold": 1.5},
    {"retrieval_final_top_k": 0}, {"embedding_dims": 3}, {"not_a_setting": 1},
])
async def test_invalid_values_are_rejected_and_change_nothing(client, patch):
    before = dict(settings.__dict__)
    assert (await client.put("/settings", json=patch)).status_code == 422
    assert settings.__dict__ == before


async def test_llm_changes_apply_from_the_next_call(client):
    first = get_llm_provider()
    res = await client.put("/settings", json={"llm_temperature": 0.9, "groq_llm_model": "openai/gpt-oss-20b"})
    assert res.json()["changed"] == ["groq_llm_model", "llm_temperature"]
    provider = get_llm_provider()
    primary = getattr(provider, "primary", provider)
    assert provider is not first and primary.model == "openai/gpt-oss-20b" and primary.temperature == 0.9


async def test_advanced_rag_changes_are_read_by_retrieval(client):
    await client.put("/settings", json={"retrieval_final_top_k": 2, "gate_vector_threshold": 0.9})
    assert settings.retrieval_final_top_k == 2 and settings.gate_vector_threshold == 0.9


async def test_groq_stays_the_default_provider(client):
    assert settings.llm_provider == "groq"
    assert settings_store.DEFAULTS["llm_provider"] == "groq"


# --- test connection ---
async def test_connection_failure_message_is_generic(client, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")  # no key: fails before any request (tests stay offline)
    res = await client.post("/settings/test", json={"target": "llm", "llm_provider": "groq", "groq_api_key": ""})
    assert res.json() == {"ok": False, "message": "Couldn't connect. Check the API key and settings, then try again."}


async def test_jev_connection_fails_cleanly_while_the_adapter_is_pending(client):
    res = await client.post("/settings/test", json={"target": "jev", "jev_base_url": "http://x"})
    assert res.json()["ok"] is False


async def test_embedding_test_uses_unsaved_values_and_reports_success(client, monkeypatch):
    seen = {}

    class Ok:
        def __init__(self, key, model, dims, *a):
            seen.update(key=key, model=model, dims=dims)

        async def embed_query(self, text):
            return [0.0]

        async def aclose(self):
            pass

    monkeypatch.setattr("app.api.settings.GeminiEmbeddingProvider", Ok)
    res = await client.post("/settings/test", json={"target": "embedding", "gemini_api_key": "typed", "embedding_model": "m2"})
    assert res.json() == {"ok": True, "message": "Connected."}
    assert seen == {"key": "typed", "model": "m2", "dims": settings.embedding_dims}


# --- re-index ---
async def test_changing_the_embedding_model_flags_projects_and_blocks_chat(client, ingest, sessions):
    pid = await _project(client, ingest)
    res = await client.put("/settings", json={"embedding_model": "gemini-embedding-002"})
    assert res.json()["needs_reindex"] == 1
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "needs_reindex"
    chat = await client.post(f"/projects/{pid}/conversations", json={"content": "validate_token"})
    assert chat.status_code == 409
    up = await client.post(f"/projects/{pid}/upload", files=[("files", ("x.py", b"x=1"))], data={"paths": ["x.py"]})
    assert up.status_code == 409


async def test_changing_a_non_embedding_setting_does_not_flag_projects(client, ingest):
    pid = await _project(client, ingest)
    await client.put("/settings", json={"retrieval_leg_k": 8, "llm_temperature": 0.3})
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "ready"


async def test_reindex_re_embeds_stored_chunks_with_the_same_ids_and_chat_works_again(client, ingest, sessions, _search_backend):
    pid = await _project(client, ingest)
    async with sessions() as s:
        ids_before = {str(c) for c in (await s.scalars(select(Chunk.id))).all()}
    old_fp = _search_backend.fingerprint
    assert await indexer.count_docs(old_fp, None) == len(ids_before)

    await client.put("/settings", json={"embedding_model": "gemini-embedding-002"})
    new = FakeEmbeddingProvider()
    new.model = "hash2"  # a different fingerprint, still offline
    set_embedding_provider(new)

    res = await client.post(f"/projects/{pid}/reindex")
    assert res.status_code == 202
    await reindex.wait(__import__("uuid").UUID(pid))
    progress = (await client.get(f"/projects/{pid}/reindex")).json()
    assert progress["state"] == "done" and progress["done"] == progress["total"] == len(ids_before)

    project = (await client.get(f"/projects/{pid}")).json()
    assert project["status"] == "ready"
    async with sessions() as s:
        assert (await s.get(Project, __import__("uuid").UUID(pid))).embedding_fingerprint == new.fingerprint
        assert {str(c) for c in (await s.scalars(select(Chunk.id))).all()} == ids_before  # nothing re-chunked
    assert await indexer.count_docs(new.fingerprint, None) == len(ids_before)
    assert await indexer.count_docs(old_fp, None) == 0  # the old index no longer holds this project
    assert ("documents", len(ids_before)) in new.calls or sum(n for k, n in new.calls if k == "documents") == len(ids_before)

    answer = await client.post(f"/projects/{pid}/conversations", json={"content": "validate_token decode token"})
    assert answer.status_code == 200 and "event: done" in answer.text


async def test_reindex_is_refused_when_not_needed(client, ingest):
    pid = await _project(client, ingest)
    assert (await client.post(f"/projects/{pid}/reindex")).status_code == 409


async def test_reindex_failure_keeps_the_project_flagged_and_resumable(client, ingest, sessions, monkeypatch):
    pid = await _project(client, ingest)
    await client.put("/settings", json={"embedding_model": "gemini-embedding-002"})
    new = FakeEmbeddingProvider()
    new.model = "hash2"
    set_embedding_provider(new)

    async def boom(texts):
        raise RuntimeError("quota")

    original = new.embed_documents
    new.embed_documents = boom
    await client.post(f"/projects/{pid}/reindex")
    await reindex.wait(__import__("uuid").UUID(pid))
    prog = (await client.get(f"/projects/{pid}/reindex")).json()
    assert prog["state"] == "error" and "quota" not in json.dumps(prog)
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "needs_reindex"

    new.embed_documents = original  # quota is back: the retry completes
    await client.post(f"/projects/{pid}/reindex")
    await reindex.wait(__import__("uuid").UUID(pid))
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "ready"


async def test_restoring_the_embedding_settings_releases_the_flag(client, ingest, sessions):
    pid = await _project(client, ingest)
    async with sessions() as s:  # pretend the project was embedded with the configured Gemini model
        project = await s.get(Project, __import__("uuid").UUID(pid))
        project.embedding_fingerprint = fingerprint_of("gemini", settings.embedding_model, settings.embedding_dims)
        await s.commit()
    assert (await client.put("/settings", json={"embedding_model": "gemini-embedding-002"})).json()["needs_reindex"] == 1
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "needs_reindex"
    await client.put("/settings", json={"embedding_model": "gemini-embedding-001"})
    assert (await client.get(f"/projects/{pid}")).json()["status"] == "ready"


async def test_dev_mode_can_be_switched_from_settings_and_gates_dev_tools(client):
    await client.put("/settings", json={"dev_mode": False})
    assert (await client.get("/config")).json() == {"dev_mode": False}
    assert (await client.get("/settings")).json()["values"]["dev_mode"] is False
    await client.put("/settings", json={"dev_mode": True})
    assert (await client.get("/config")).json() == {"dev_mode": True}


async def test_embedding_key_is_separate_and_masked(client):
    from app.providers.embeddings import get_embedding_provider
    await client.put("/settings", json={"gemini_api_key": "chat-key-1111", "embedding_api_key": "embed-key-2222"})
    body = (await client.get("/settings")).json()
    assert body["secrets"]["embedding_api_key"] == {"set": True, "masked": "••••2222"}
    assert get_embedding_provider().api_key == "embed-key-2222"
    settings.embedding_api_key = ""  # empty: falls back to the Gemini key
    set_embedding_provider(None)
    assert get_embedding_provider().api_key == "chat-key-1111"
