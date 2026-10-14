"""Embedding provider wrapper. Nothing else in the app talks to Gemini directly.

Chunks are embedded with the RETRIEVAL_DOCUMENT task type and questions with RETRIEVAL_QUERY.
Calls are paced by the shared provider-agnostic limiter (app/providers/rate_limit.py) so 429s are
rare, batched by token budget, and still retried with backoff on HTTP 429 (and transient 5xx)."""
import asyncio
import logging
import re
from collections import OrderedDict
from typing import Protocol

import httpx

from app.config import settings
from app.providers.rate_limit import RateLimiter, batch_by_budget, estimate_tokens, get_limiter

log = logging.getLogger("devdocs.embeddings")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
QUERY_CACHE_SIZE = 256


class EmbeddingError(Exception):
    """Embedding failed after retries. The message is internal; users only see a generic failure."""


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dims: int

    @property
    def fingerprint(self) -> str: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def fingerprint_of(provider: str, model: str, dims: int) -> str:
    return f"{provider}:{model}:{dims}"


class GeminiEmbeddingProvider:
    provider = "gemini"

    def __init__(
        self, api_key: str, model: str, dims: int, batch_size: int, max_retries: int, backoff: float,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: RateLimiter | None = None,
    ):
        self.api_key, self.model, self.dims = api_key, model, dims
        self.transport = transport  # tests inject a mock transport; None uses the network
        self.batch_size, self.max_retries, self.backoff = batch_size, max_retries, backoff
        self.limiter = limiter or RateLimiter()  # unlimited unless one is supplied
        # Query path: one keep-alive connection (no TLS handshake per question) and a small LRU, because a
        # repeated question (Retry, Regenerate, the same follow-up) must not cost an API call or ~1 s.
        self._query_client: httpx.AsyncClient | None = None
        self._query_loop: asyncio.AbstractEventLoop | None = None
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.provider, self.model, self.dims)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for batch in batch_by_budget(texts, self.batch_size, self.limiter.max_batch_tokens):
                vectors += await self._batch(client, batch, "RETRIEVAL_DOCUMENT")
        return vectors

    def _client_for_queries(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._query_client is None or self._query_client.is_closed or self._query_loop is not loop:
            self._query_client = httpx.AsyncClient(timeout=60, transport=self.transport)
            self._query_loop = loop
        return self._query_client

    async def aclose(self) -> None:
        if self._query_client is not None and not self._query_client.is_closed:
            await self._query_client.aclose()

    async def embed_query(self, text: str) -> list[float]:
        cached = self._query_cache.get(text)
        if cached is not None:
            self._query_cache.move_to_end(text)
            return cached
        vector = (await self._batch(self._client_for_queries(), [text], "RETRIEVAL_QUERY"))[0]
        self._query_cache[text] = vector
        while len(self._query_cache) > QUERY_CACHE_SIZE:
            self._query_cache.popitem(last=False)
        return vector

    async def _batch(self, client: httpx.AsyncClient, texts: list[str], task: str) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingError("GEMINI_API_KEY is not set")
        model_path = f"models/{self.model}"
        body = {"requests": [
            {"model": model_path, "content": {"parts": [{"text": t}]}, "taskType": task,
             "outputDimensionality": self.dims}
            for t in texts
        ]}
        url = f"{GEMINI_BASE}/{model_path}:batchEmbedContents"
        # Gemini counts each text in a batch as one request toward its per-minute quota.
        await self.limiter.acquire(requests=len(texts), tokens=sum(estimate_tokens(t) for t in texts))
        headers = {"x-goog-api-key": self.api_key}
        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise EmbeddingError(f"embedding request failed: {exc!r}") from exc
                await asyncio.sleep(self.backoff * 2 ** attempt)
                continue
            if resp.status_code == 200:
                vectors = [e["values"] for e in resp.json()["embeddings"]]
                if len(vectors) != len(texts) or any(len(v) != self.dims for v in vectors):
                    raise EmbeddingError("embedding response did not match the request")
                return vectors
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if not retryable or attempt == self.max_retries:
                raise EmbeddingError(f"embedding request failed: HTTP {resp.status_code} {resp.text[:200]}")
            wait = _retry_after(resp) or self.backoff * 2 ** attempt
            log.warning("embedding HTTP %s, retrying in %.1fs (%d/%d)", resp.status_code, wait, attempt + 1, self.max_retries)
            await asyncio.sleep(wait)
        raise EmbeddingError("unreachable")  # pragma: no cover


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after", "")
    return float(value) if re.fullmatch(r"\d+(\.\d+)?", value) else None


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = GeminiEmbeddingProvider(
            settings.embedding_api_key or settings.gemini_api_key, settings.embedding_model, settings.embedding_dims,
            settings.embedding_batch_size, settings.embedding_max_retries, settings.embedding_backoff_seconds,
            limiter=get_limiter("gemini_embedding"),
        )
    return _provider


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """Swap the provider (tests use a fake; `None` goes back to the configured Gemini one)."""
    global _provider
    _provider = provider
