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
    # Key used for embeddings only; empty = use gemini_api_key. Lets chat and embeddings use different accounts.
    embedding_api_key: str = ""
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
    groq_llm_fallback_rpm: int = 0
    groq_llm_fallback_tpm: int = 7000  # Groq limits are per model: the fallback model has its own budget

    # Chat LLM (Phase 7). Both providers speak the OpenAI chat-completions protocol; only URL, key and model
    # differ. The switch is read at backend start. The key of the active provider must be set.
    llm_provider: str = "groq"  # groq | gemini
    groq_api_key: str = ""
    groq_llm_model: str = "openai/gpt-oss-120b"
    gemini_llm_model: str = "gemini-3.5-flash-lite"
    # Second Groq model used only when the primary's token budget would make a question wait longer than
    # `llm_fallback_wait_seconds` (empty = never). It has its own per-minute budget, so back-to-back
    # questions no longer stall for tens of seconds, and gpt-oss-20b also answers faster.
    groq_fallback_model: str = "openai/gpt-oss-20b"
    llm_fallback_wait_seconds: float = 6.0
    llm_max_output_tokens: int = 1500  # a safety ceiling, not a target length (reasoning tokens count too)
    llm_max_retries: int = 2  # the SDK's own retries on 429 / 5xx / connection errors, before the first token
    llm_timeout_seconds: float = 60.0
    llm_reasoning_effort: str = "low"  # low | medium | high; reasoning tokens are paid for but never shown
    # Conversation context sent with each question: the most recent messages, capped by count and tokens.
    chat_history_messages: int = 6
    chat_history_tokens: int = 800
    chat_max_message_chars: int = 8000
    llm_temperature: float = 0.2  # low: answers are grounded explanations, not creative writing
    # Resilience (Phase 10): app-level retries before the first token (on top of the SDK's own), and how long
    # the stream may stay silent before the call is treated as hung.
    generation_retries: int = 1
    llm_inactivity_seconds: float = 45.0
    # Conversation memory (Phase 9): once the messages after the checkpoint exceed this many tokens, older
    # ones are folded into the summary in the background; the newest `summary_keep_messages` stay verbatim.
    summary_threshold_tokens: int = 1200
    summary_keep_messages: int = 2
    summary_max_tokens: int = 400
    # Dev-only fault injection (honoured only with DEV_MODE=true): "", "once" (first attempt of a turn fails)
    # or "always" (every attempt fails).
    fault_llm: str = ""
    fault_retrieval: str = ""

    # Hybrid retrieval (Phase 6). See Docs/Plan/Retrieval.md for how each value was chosen.
    retrieval_leg_k: int = 10  # Top-K asked of each of BM25 and kNN
    rrf_k: int = 60
    retrieval_final_top_k: int = 5
    # RRF-score multiplier for chunks of test files, unless the question mentions tests (1 = off).
    retrieval_test_path_factor: float = 0.8
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
    # JevReranker ("System One") connection; used only when reranker=jev. The adapter itself waits for its API.
    jev_base_url: str = ""
    jev_model: str = ""
    jev_api_key: str = ""

    # Prefix of the per-fingerprint ES index name; tests override it so they never touch real data.
    es_index_prefix: str = "devdocs-chunks"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
