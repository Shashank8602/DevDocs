"""Settings endpoints (Phase 11): read (keys masked), save, and Test Connection. Saving applies from the next
query; changing the embedding model or dimensions marks the affected projects `needs_reindex`."""
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.config import settings
from app.db import get_session
from app.ingestion.status import refresh_project_status
from app.models import Project
from app.providers.embeddings import GeminiEmbeddingProvider, fingerprint_of, get_embedding_provider
from app.providers.llm import PROVIDERS, OpenAICompatLLMProvider
from app.retrieval.reranker import JevReranker

router = APIRouter(prefix="/settings", tags=["settings"])
log = logging.getLogger("devdocs.settings")

CONNECT_FAILED = "Couldn't connect. Check the API key and settings, then try again."


async def sync_reindex_flags(session: AsyncSession) -> int:
    """Projects embedded with another fingerprint than the configured one need a re-index; projects whose
    stored fingerprint matches again are released. Returns how many are flagged. The caller commits."""
    fingerprint = get_embedding_provider().fingerprint
    flagged = 0
    for project in (await session.scalars(select(Project))).all():
        if project.embedding_fingerprint is None or project.status == "empty":
            continue
        if project.embedding_fingerprint != fingerprint:
            project.status = "needs_reindex"
            flagged += 1
        elif project.status == "needs_reindex":
            project.status = "ready"
            await refresh_project_status(session, project.id)
    return flagged


@router.get("")
async def read_settings():
    return settings_store.public_view()


@router.put("")
async def save_settings(patch: settings_store.SettingsPatch, session: AsyncSession = Depends(get_session)):
    changed = await settings_store.save(session, patch)
    flagged = 0
    if changed & set(settings_store.EMBEDDING_FIELDS):
        flagged = await sync_reindex_flags(session)
    await session.commit()
    return {**settings_store.public_view(), "changed": sorted(changed), "needs_reindex": flagged}


class TestRequest(BaseModel):
    """Unsaved form values may be tested before saving; anything left out falls back to the saved value."""
    model_config = ConfigDict(extra="forbid")

    target: Literal["llm", "embedding", "jev"]
    llm_provider: str | None = None
    groq_llm_model: str | None = None
    gemini_llm_model: str | None = None
    embedding_model: str | None = None
    embedding_dims: int | None = None
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    embedding_api_key: str | None = None
    jev_base_url: str | None = None
    jev_model: str | None = None
    jev_api_key: str | None = None


def _pick(value, current):
    return value if value not in (None, "") else current


async def _test_llm(req: TestRequest) -> None:
    provider = _pick(req.llm_provider, settings.llm_provider)
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    key = _pick(req.groq_api_key if provider == "groq" else req.gemini_api_key,
                settings.groq_api_key if provider == "groq" else settings.gemini_api_key)
    model = _pick(req.groq_llm_model if provider == "groq" else req.gemini_llm_model,
                  settings.groq_llm_model if provider == "groq" else settings.gemini_llm_model)
    llm = OpenAICompatLLMProvider(provider, key, model, 32, 0, 20.0)
    stream = llm.stream([{"role": "user", "content": "Reply with the single word: ok"}])
    try:
        async for _ in stream:
            break  # one token proves the key, the model name and the route
    finally:
        await stream.aclose()


async def _test_embedding(req: TestRequest) -> None:
    key = _pick(req.embedding_api_key, settings.embedding_api_key) or _pick(req.gemini_api_key, settings.gemini_api_key)
    model = _pick(req.embedding_model, settings.embedding_model)
    dims = _pick(req.embedding_dims, settings.embedding_dims)
    provider = GeminiEmbeddingProvider(key, model, dims, 1, 0, 0.0)
    try:
        await provider.embed_query("connection test")
    finally:
        await provider.aclose()


async def _test_jev(req: TestRequest) -> None:
    await JevReranker(
        _pick(req.jev_base_url, settings.jev_base_url), _pick(req.jev_model, settings.jev_model),
        _pick(req.jev_api_key, settings.jev_api_key),
    ).ping()


@router.post("/test")
async def test_connection(req: TestRequest):
    """Never raises and never echoes provider error text: failures say the same generic thing."""
    try:
        await {"llm": _test_llm, "embedding": _test_embedding, "jev": _test_jev}[req.target](req)
    except Exception as exc:
        log.info("connection test (%s) failed: %r", req.target, exc)
        return {"ok": False, "message": CONNECT_FAILED}
    return {"ok": True, "message": "Connected."}


# fingerprint preview, so the page can say "this change needs a re-index" before saving
@router.get("/embedding-fingerprint")
async def embedding_fingerprint():
    return {"fingerprint": fingerprint_of("gemini", settings.embedding_model, settings.embedding_dims)}
