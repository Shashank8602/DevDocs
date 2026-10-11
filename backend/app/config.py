from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://devdocs:devdocs@localhost:5432/devdocs"
    elasticsearch_url: str = "http://localhost:9200"
    cors_origins: str = "http://localhost:5173"
    dev_mode: bool = False
    data_dir: Path = REPO_ROOT / "data"

    # Embeddings (Gemini). Keys and model come from .env for now; a Settings UI replaces this later.
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dims: int = 1536
    embedding_batch_size: int = 50  # texts per request (Gemini allows up to 100)
    embedding_max_retries: int = 5  # extra attempts after HTTP 429 / transient 5xx
    embedding_backoff_seconds: float = 2.0  # first wait; doubles each retry unless Retry-After is sent
    # Client-side rate limits per provider key (see app/providers/rate_limit.py): requests and tokens
    # per minute, 0 = unlimited. Set each just under that API key's real quota. Env: GEMINI_EMBEDDING_RPM ...
    gemini_embedding_rpm: int = 90  # Gemini counts every text of an embedding batch as a request
    gemini_embedding_tpm: int = 25000
    gemini_llm_rpm: int = 0
    gemini_llm_tpm: int = 0
    groq_llm_rpm: int = 0
    groq_llm_tpm: int = 7000  # the current Groq key allows 8,000 tokens per minute

    # Hybrid retrieval (Phase 6). See Docs/Plan/Retrieval.md for how each value was chosen.
    retrieval_leg_k: int = 10  # Top-K asked of each of BM25 and kNN
    rrf_k: int = 60
    retrieval_final_top_k: int = 5
    retrieval_context_tokens: int = 3000  # sized for Groq's 8,000 tokens-per-minute limit
    retrieval_max_retries: int = 2  # extra attempts after an ES connection error / 429 / 5xx
    retrieval_backoff_seconds: float = 0.25
    # Refusal gate: the question is answered only if the best vector similarity (Elasticsearch's cosine
    # score, (1 + cosine) / 2) reaches this. Tied to the embedding fingerprint (gemini-embedding-001 at
    # 1,536 dims); changing the embedding config means re-running `python -m eval.run` to re-pick it.
    gate_vector_threshold: float = 0.82
    # Precision filter: keep a chunk only if its vector similarity is within this of the best chunk's.
    # 0 disables the filter. The `bm25_protect` best BM25 hits are always kept, so exact identifier and
    # config-name matches that embed poorly are not cut. See Docs/Reports/Retrieval-Strategy-Decision.md.
    relative_cutoff: float = 0.04
    bm25_protect: int = 2
    # Optional reranker (off by default: it did not earn its latency, see the decision report). When
    # rerank_top_n > 0 the first N RRF candidates are scored and put first in score order; the gate and
    # the filter are unchanged. The reranker's scale is the model's own, so no threshold is applied to it.
    rerank_top_n: int = 0
    reranker: str = "cross_encoder"  # cross_encoder | jev (adapter pending)
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"  # fastembed cross-encoder, runs locally

    # Prefix of the per-fingerprint ES index name; tests override it so they never touch real data.
    es_index_prefix: str = "devdocs-chunks"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
