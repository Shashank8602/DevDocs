"""Integration fixtures: a real Postgres (docker-compose) with a separate test database."""
import asyncio
import uuid

import hashlib
import re

import httpx
import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import auth as auth_api
from app.config import settings
from app.db import Base, get_session, get_session_factory
from app.ingestion import worker
from app.main import app
from app.models import User
from app.providers.llm import LLMError, set_llm_provider
from app.providers.embeddings import fingerprint_of, set_embedding_provider
from app.search import es_client, index_manager
from app.security import hash_password, make_token

TEST_URL = make_url(settings.database_url).set(database="devdocs_test")


async def _ensure_test_database() -> None:
    admin = create_async_engine(
        make_url(settings.database_url).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    async with admin.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'devdocs_test'"))
        if not exists:
            await conn.execute(text("CREATE DATABASE devdocs_test"))
    await admin.dispose()


# Words that mean the same thing to the fake embedder, so "verify identity" lands near "validate token".
CONCEPTS = {
    "verify": "auth", "verification": "auth", "validate": "auth", "validation": "auth", "identity": "auth",
    "authenticate": "auth", "authentication": "auth", "token": "auth", "login": "auth", "credentials": "auth",
    "payment": "pay", "charge": "pay", "invoice": "pay", "refund": "pay", "card": "pay",
}


class FakeEmbeddingProvider:
    """Deterministic, offline: a hashed bag of concepts, unit-length. No network, no API key."""
    provider, model, dims = "fake", "hash", 64

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.provider, self.model, self.dims)

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for w in re.findall(r"\w+", text.lower()) or ["_empty"]:
            slot = int(hashlib.md5(CONCEPTS.get(w, w).encode()).hexdigest(), 16) % self.dims
            v[slot] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts):
        self.calls.append(("documents", len(texts)))
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        self.calls.append(("query", 1))
        return self._vec(text)


class FakeLLMProvider:
    """Offline chat LLM. Streams `tokens`; `fail_before` raises before the first token, `fail_after`
    raises after that many tokens. `calls` records every prompt, so tests can assert the LLM was skipped."""
    provider, model = "fake", "fake-llm"

    def __init__(self):
        self.tokens = ["Authentication ", "uses ", "JWT tokens."]
        self.fail_before = False
        self.fail_after: int | None = None
        self.fail_times = 0  # raise before the first token this many times, then behave
        self.wait_seconds = 0.0  # what `wait_estimate` reports (the rate limiter's pending wait)
        self.hang = False  # emit nothing and never finish (inactivity-timeout tests)
        self.calls: list[list[dict]] = []

    def wait_estimate(self, messages, max_output_tokens=None):
        return self.wait_seconds

    async def stream(self, messages, max_output_tokens=None):
        self.calls.append(messages)
        if self.hang:
            await asyncio.Event().wait()
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError("HTTP 500 provider exploded (internal detail)")
        if self.fail_before:
            raise LLMError("HTTP 500 provider exploded (internal detail)")
        for i, token in enumerate(self.tokens):
            if self.fail_after is not None and i >= self.fail_after:
                raise LLMError("stream broke (internal detail)")
            yield token


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path):
    """Settings tests change the shared `settings` object; restore it, and keep secret.key out of data/."""
    snapshot = dict(settings.__dict__)
    settings.data_dir = tmp_path / "data"
    yield
    settings.__dict__.update(snapshot)


@pytest.fixture(autouse=True)
def llm():
    fake = FakeLLMProvider()
    set_llm_provider(fake)
    yield fake
    set_llm_provider(None)


@pytest_asyncio.fixture(autouse=True)
async def _search_backend(monkeypatch):
    """Fake embeddings + a fresh ES client per test (one event loop each) on `test-` prefixed indices."""
    monkeypatch.setattr(settings, "es_index_prefix", "test-devdocs")
    client = AsyncElasticsearch(settings.elasticsearch_url)
    monkeypatch.setattr(es_client, "es", client)
    index_manager._ensured.clear()
    provider = FakeEmbeddingProvider()
    set_embedding_provider(provider)

    async def drop_test_indices():
        found = await client.indices.get(index="test-devdocs-*", expand_wildcards="open", allow_no_indices=True)
        for name in found.keys():
            await client.indices.delete(index=name)

    await drop_test_indices()
    yield provider
    await drop_test_indices()
    await client.close()
    set_embedding_provider(None)


@pytest.fixture(autouse=True)
def _no_background_worker(monkeypatch):
    """Uploads would start the real background worker; tests run ingestion explicitly via `ingest`."""
    queued: list = []
    monkeypatch.setattr(worker, "enqueue", queued.append)
    return queued


@pytest_asyncio.fixture
async def sessions():
    await _ensure_test_database()
    engine = create_async_engine(TEST_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def ingest(sessions):
    """Run the background worker's job for a project to completion, in-test."""
    async def run(project_id):
        await worker.process_project(uuid.UUID(str(project_id)), sessions)
    return run


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    auth_api.reset_throttle()


async def make_user(sessions, email: str, role: str = "member", password: str = "password-123") -> User:
    """A throwaway person created inside a test; nothing about users is seeded anywhere in the app."""
    async with sessions() as session:
        user = User(email=email, name=email.split("@")[0], password_hash=hash_password(password), role=role)
        session.add(user)
        await session.commit()
        return user


async def add_member(client, project_id, user) -> None:
    """Put an existing person into one project (the admin client does this through the API)."""
    res = await client.post(f"/projects/{project_id}/members", json={"email": user.email})
    assert res.status_code == 201, res.text


@pytest_asyncio.fixture
async def admin(sessions):
    return await make_user(sessions, "admin@example.test", "admin")


@pytest_asyncio.fixture
async def http(sessions):
    """Build an HTTP client for any user (or none): `http(user)`. Each client carries its own credentials."""
    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_session_factory] = lambda: sessions
    opened: list[httpx.AsyncClient] = []

    def build(user: User | None = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {make_token(user.id)}"} if user else {}
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)
        opened.append(c)
        return c

    yield build
    for c in opened:
        await c.aclose()
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(http, admin):
    """The default client acts as an admin, so the feature tests keep their original shape."""
    return http(admin)
