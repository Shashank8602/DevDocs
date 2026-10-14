"""Reranker interface. `CrossEncoderReranker` (fastembed, local ONNX) is the default; `JevReranker`
("System One") is an adapter to finish once its API details are provided."""
import asyncio
import logging
from typing import Protocol

from app.config import settings

log = logging.getLogger("devdocs.reranker")


class RerankError(Exception):
    """Reranking failed. The pipeline turns this into a RetrievalError."""


class Reranker(Protocol):
    name: str

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """One score per passage, in input order. Higher = more relevant. The scale is the model's own,
        which is why no fixed threshold is applied to it: it only orders the candidates."""
        ...


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        self.name = model_name
        self._model = None  # loaded on first use: the first call may download the model (~90 MB)

    def _load(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder  # heavy import, kept lazy

            log.info("loading reranker model %s", self.name)
            self._model = TextCrossEncoder(self.name)
        return self._model

    def _score_sync(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._load().rerank(query, passages)]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        try:
            return await asyncio.to_thread(self._score_sync, query, passages)  # CPU-bound: keep the loop free
        except Exception as exc:
            raise RerankError(f"reranking failed: {exc!r}") from exc


class JevReranker:
    """Placeholder until the Jev ("System One") API details are provided. Its settings (base URL, model, key)
    are already stored and editable; only the request / response mapping below is missing."""
    name = "jev"

    def __init__(self, base_url: str = "", model: str = "", api_key: str = ""):
        self.base_url, self.model, self.api_key = base_url, model, api_key

    async def ping(self) -> None:
        """Connection check used by Settings -> Test Connection."""
        if not self.base_url:
            raise RerankError("Jev base URL is not set")
        raise RerankError("JevReranker is not implemented yet (waiting for its API details)")

    async def score(self, query: str, passages: list[str]) -> list[float]:
        raise RerankError("JevReranker is not implemented yet (waiting for its API details)")


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = (
            JevReranker(settings.jev_base_url, settings.jev_model, settings.jev_api_key)
            if settings.reranker == "jev" else CrossEncoderReranker(settings.reranker_model)
        )
    return _reranker


def set_reranker(reranker: Reranker | None) -> None:
    """Swap the reranker (tests use a fake; `None` goes back to the configured one)."""
    global _reranker
    _reranker = reranker
