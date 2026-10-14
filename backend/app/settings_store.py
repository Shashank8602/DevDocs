"""User-editable configuration (Phase 11): validation, encrypted storage of API keys, and applying values
to the running process.

`.env` provides the defaults; whatever the user saves in the Settings page is stored in one Postgres row
(`settings`, id = 1) and wins from the next query on. Values are applied by updating the shared `settings`
object and resetting the provider caches, so nothing needs a restart. API keys are Fernet-encrypted with
`data/secret.key` (created on first use) and are never returned in clear: the API only reports whether a
key is set and its last four characters."""
import logging
import os
from typing import Annotated, Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSettings

log = logging.getLogger("devdocs.settings")

SECRET_FIELDS = ("groq_api_key", "gemini_api_key", "embedding_api_key", "jev_api_key")
_PREFIX = "enc:"


class SettingsPatch(BaseModel):
    """A partial update. Unknown keys are rejected; every field is range-checked."""
    model_config = ConfigDict(extra="forbid")

    # General
    dev_mode: bool | None = None
    # LLM
    llm_provider: Literal["groq", "gemini"] | None = None
    groq_llm_model: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    gemini_llm_model: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    groq_fallback_model: Annotated[str, Field(max_length=100)] | None = None
    llm_temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    llm_max_output_tokens: Annotated[int, Field(ge=64, le=8192)] | None = None
    llm_reasoning_effort: Literal["low", "medium", "high"] | None = None
    groq_api_key: Annotated[str, Field(max_length=300)] | None = None
    gemini_api_key: Annotated[str, Field(max_length=300)] | None = None
    embedding_api_key: Annotated[str, Field(max_length=300)] | None = None
    # Embeddings
    embedding_model: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    embedding_dims: Annotated[int, Field(ge=64, le=3072)] | None = None
    # Reranker
    reranker: Literal["cross_encoder", "jev"] | None = None
    reranker_model: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    rerank_top_n: Annotated[int, Field(ge=0, le=50)] | None = None
    jev_base_url: Annotated[str, Field(max_length=300)] | None = None
    jev_model: Annotated[str, Field(max_length=100)] | None = None
    jev_api_key: Annotated[str, Field(max_length=300)] | None = None
    # Advanced RAG
    retrieval_leg_k: Annotated[int, Field(ge=1, le=50)] | None = None
    rrf_k: Annotated[int, Field(ge=1, le=200)] | None = None
    retrieval_final_top_k: Annotated[int, Field(ge=1, le=20)] | None = None
    retrieval_test_path_factor: Annotated[float, Field(ge=0.1, le=1)] | None = None
    retrieval_context_tokens: Annotated[int, Field(ge=500, le=8000)] | None = None
    gate_vector_threshold: Annotated[float, Field(ge=0, le=1)] | None = None
    relative_cutoff: Annotated[float, Field(ge=0, le=0.5)] | None = None
    bm25_protect: Annotated[int, Field(ge=0, le=10)] | None = None


FIELDS = tuple(SettingsPatch.model_fields)
PLAIN_FIELDS = tuple(f for f in FIELDS if f not in SECRET_FIELDS)
EMBEDDING_FIELDS = ("embedding_model", "embedding_dims")

# What the process started with (.env + code defaults): the target of "reset to defaults".
DEFAULTS = {f: getattr(settings, f) for f in FIELDS}


def _fernet() -> Fernet:
    path = settings.data_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        try:
            os.chmod(path, 0o600)
        except OSError:  # Windows: permissions are not POSIX
            pass
    return Fernet(path.read_bytes().strip())


def encrypt(value: str) -> str:
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(_PREFIX):
        return stored
    try:
        return _fernet().decrypt(stored[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("a stored API key could not be decrypted (secret.key changed?); ignoring it")
        return ""


def mask(value: str) -> str:
    return "" if not value else "••••" + value[-4:]


async def _row(session: AsyncSession) -> AppSettings:
    row = await session.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1, data={})
        session.add(row)
        await session.flush()
    return row


def reset_runtime_caches() -> None:
    """Providers and the reranker are built from `settings`; drop them so the next call rebuilds them."""
    from app.providers.embeddings import set_embedding_provider
    from app.providers.llm import set_llm_provider
    from app.retrieval.reranker import set_reranker

    set_llm_provider(None)
    set_embedding_provider(None)
    set_reranker(None)


def _apply(values: dict) -> None:
    for key, value in values.items():
        if key in FIELDS and value is not None:
            setattr(settings, key, value)


async def load_overrides(session: AsyncSession) -> None:
    """Startup: apply the saved values on top of `.env`."""
    row = await session.get(AppSettings, 1)
    if row is None:
        return
    values = {k: (decrypt(v) if k in SECRET_FIELDS else v) for k, v in row.data.items() if k in FIELDS}
    _apply({k: v for k, v in values.items() if not (k in SECRET_FIELDS and not v)})
    reset_runtime_caches()


def public_view() -> dict:
    """Current effective values for the Settings page. Keys are masked, never returned in clear."""
    return {
        "values": {f: getattr(settings, f) for f in PLAIN_FIELDS},
        "defaults": {f: DEFAULTS[f] for f in PLAIN_FIELDS},
        "secrets": {
            f: {"set": bool(getattr(settings, f)), "masked": mask(getattr(settings, f))} for f in SECRET_FIELDS
        },
    }


async def save(session: AsyncSession, patch: SettingsPatch) -> set[str]:
    """Validate-and-store a partial update, apply it, and return the names that changed. The caller
    commits. An empty string for an API key is ignored (keeps the stored key), so a masked field that
    was never retyped cannot wipe it."""
    incoming = {k: v for k, v in patch.model_dump(exclude_none=True).items()}
    incoming = {k: v for k, v in incoming.items() if not (k in SECRET_FIELDS and not v.strip())}
    changed = {k for k, v in incoming.items() if getattr(settings, k) != v}
    row = await _row(session)
    stored = dict(row.data)
    for key, value in incoming.items():
        stored[key] = encrypt(value) if key in SECRET_FIELDS else value
    row.data = stored
    _apply(incoming)
    if changed:
        reset_runtime_caches()
    return changed
