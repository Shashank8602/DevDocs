# DevDocs V1 — Implementation Plan

## Context

DevDocs is a project-scoped RAG assistant for software projects: the user uploads a project folder, DevDocs ingests it incrementally (SHA-256 change detection), and the user chats with it to understand the code, with deterministic, clickable citations. The 13 spec PDFs under `Docs/Features/` fully describe V1 behaviour; the repo has no code yet (greenfield, not a git repo). This plan turns those specs into a buildable architecture and a phased implementation order.

Decisions confirmed with the user:
- **Local single-user app**: no auth; FastAPI + React running locally. The ₹0 target now applies to infrastructure only (Postgres, Elasticsearch, local reranker); Groq and Gemini API calls are metered and subject to rate limits (see "Cost / limits" under Tech Stack).
- **Providers (changed from OpenAI, 2026-10-10):** the answer LLM is **Groq** by default (`openai/gpt-oss-120b`, via Groq's OpenAI-compatible endpoint) with **Gemini** as a switchable alternative (`LLM_PROVIDER=groq|gemini` in `.env`; a Settings UI toggle comes later), and embeddings are **Google Gemini** (`gemini-embedding-001`, 1536 dimensions). Groq has no embeddings endpoint, so these are two separate providers with two API keys. The Settings page still has free-form provider/model/API-key fields (no hard-coded catalog), configured separately for the LLM and for embeddings; an optional base URL lets the LLM point at any OpenAI-compatible endpoint.
- **Jev reranker** is a new "System One" decision model whose API we don't have yet → build the `Reranker` interface with the Cross-Encoder as the default. Add a `JevReranker` adapter that is selectable in config/Settings; finish its HTTP call once its API details are provided.

### Spec conflicts (resolved by taking the later-dated spec)
| Topic | Earlier spec | Later spec (wins) |
|---|---|---|
| Streaming / Stop | Chat Interface: future | RAG Answer Generation + States: **V1** |
| Clickable citations, scroll + highlight | Chat/Explorer: future | Citation & Source Display: **V1** |
| Non-code citation label | Chat: "README.md — Section", "pdf — Page 7" | Citation spec: **path only**. We still store page/section metadata for later use |
| Input while generating | Disabled | Disabled, plus a **Stop** button during streaming |

## Tech Stack

**Backend (Python 3.11, `backend/`)**
- FastAPI + Uvicorn, Pydantic v2, SQLAlchemy 2.0 (async, `asyncpg`) on **PostgreSQL 16**, with Alembic migrations. Foreign keys use `ON DELETE CASCADE`. Postgres is the source of truth for projects, files, chunk text and metadata, conversations, messages and settings.
- **Elasticsearch 8.x** (`elasticsearch` async Python client) is the search engine for **both** retrieval legs:
  - **BM25**: a `match` query on `content` (ES's default similarity is BM25). A custom code-aware analyzer uses `word_delimiter_graph` with `preserve_original`, so `validate_token`, `JwtService` and `jwtService` match both the whole identifier and its parts.
  - **Vectors**: a `dense_vector` field (`index: true`, cosine) queried with approximate `knn`.
  - Every query carries `filter: [{term: project_id}, {terms: file_id: ready_ids}]`, applied inside both the `match` and the `knn` query so filtering happens before ranking.
  - Index per embedding-config fingerprint (`devdocs-chunks-{fingerprint}`), because `dense_vector` dims are fixed per mapping.
  - RRF stays **in application code** (spec: internal, k=60), so it doesn't depend on ES license tiers.
- **Infra**: `docker-compose.yml` runs `postgres:16` and `elasticsearch:8.x` (single-node, `xpack.security.enabled=false`, local only, ~1 GB heap). Both are free for local use, so infrastructure stays at ₹0.
- **LLM**: one `LLMProvider` wrapper over the `openai` SDK (`AsyncOpenAI`; `chat.completions.create(stream=True)` for answers and summaries). Both supported providers speak the OpenAI chat-completions protocol, so switching only changes `base_url`, key and model. `LLM_PROVIDER` in `.env` selects which one (default `groq`):

  | `LLM_PROVIDER` | Base URL | Key | Model var (default) |
  |---|---|---|---|
  | `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `GROQ_LLM_MODEL` (`openai/gpt-oss-120b`) |
  | `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` | `GEMINI_LLM_MODEL` (`gemini-3.5-flash-lite`) |

  - `gpt-oss-120b` is a reasoning model: the streamer forwards only the final content tokens (never reasoning tokens) to the SSE stream, and reasoning effort is set low to save latency and tokens.
  - Gemini model names change quickly: `gemini-2.5-flash` is already closed to new users, so the default was picked by listing the models the key can use and timing them. The provider check script (`scripts/check_providers.py`) verifies the configured model works.
  - Measured with the check script: Groq streams token by token with a first token in ~0.3s. Through Gemini's compatibility endpoint, `gemini-3.8-flash` answered in 2 chunks with a first token after ~14–50s (too slow), while `gemini-3.5-flash-lite` took ~1s to first token (3 chunks, consistent over two runs) and is the Gemini default. Groq stays the default for chat because it streams token by token; Gemini is the fallback (for example when Groq's 8,000 tokens-per-minute limit is the bottleneck).
  - Switching provider needs no re-indexing: embeddings are always Gemini and are independent of the chat LLM. The switch is read at backend start, so restart after changing `.env`.
- **Embeddings**: Google Gemini `gemini-embedding-001` behind a thin `EmbeddingProvider` wrapper (official `google-genai` SDK), so the rest of the code never imports Groq, OpenAI or Gemini directly.
  - The model's native size is 3072; we request `output_dimensionality=1536` (Matryoshka truncation, near-full quality at half the storage), so the ES `dense_vector` mapping uses `dims: 1536` with cosine similarity (truncated vectors are not unit-length, so cosine is required).
  - Gemini uses task types: chunks are embedded with `RETRIEVAL_DOCUMENT` and questions with `RETRIEVAL_QUERY`, so `EmbeddingProvider` exposes `embed_documents()` and `embed_query()`.
  - The embedding fingerprint (provider + model + dimensions, e.g. `gemini:gemini-embedding-001:1536`) names the ES index, so changing the model or dimensions creates a separate index and triggers re-indexing.
  - Embedding calls are batched and retried with backoff on HTTP 429 rate-limit errors; batch size and backoff are configurable because free-tier limits are tight.
  - **Cost / limits**: both APIs are metered and rate-limited (check current free-tier limits on the Groq and Gemini dashboards). Usage comes from embedding each chunk once (incremental ingestion avoids re-embedding unchanged files), one embedding per question, and one answer call per question plus occasional summaries. We keep usage small with a ~3k-token retrieval context budget (the current Groq key allows 8,000 tokens per minute, so one answer call stays around 4–5k tokens including system prompt, history and the answer, leaving room for a second question in the same minute), summaries instead of full history, and skipping the LLM when no relevant context is found.
- **Cross-encoder reranker (optional, off by default)**: `fastembed` `TextCrossEncoder` (ONNX, no PyTorch), `Xenova/ms-marco-MiniLM-L-6-v2`. It runs locally and costs nothing but about 120 ms per passage. It is off by default (`RERANK_TOP_N=0`) because the vector-similarity gate and filter beat it on this project; see [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md).
- **Parsers**: `pypdf` (per page), `python-docx`, `markdown-it-py` (headings), `PyYAML`/`json`, `beautifulsoup4` (HTML text), stdlib `csv`. Python code is chunked with stdlib `ast` (function/class boundaries). Other languages use `langchain-text-splitters` `RecursiveCharacterTextSplitter.from_language`, with line tracking.
- Token estimates come from `tiktoken` (approximate counts for budgets). API keys are encrypted with `cryptography.Fernet`, using a key stored in `data/secret.key`.
- Streaming uses SSE over `StreamingResponse` (`text/event-stream`).

**Frontend (`frontend/`)**: Vite + React + TypeScript, react-router, TanStack Query, Tailwind. Rendering uses `react-markdown` + `remark-gfm` and `react-syntax-highlighter` (Prism, line numbers + per-line highlight props). File viewers use `react-pdf` for PDFs, `mammoth` (DOCX→HTML) and `papaparse` (CSV table).

## Repository Layout

```
backend/app/
  main.py, config.py (internal constants), db.py, models.py, schemas.py
  api/        projects.py files.py conversations.py settings.py ingestion.py
  ingestion/  discovery.py filters.py change_detection.py hashing.py pipeline.py worker.py
              parsers/{base,code,markdown,pdf,docx,structured,text,html,csv}.py
              chunking.py   # emits Chunk(text, line_start, line_end, page, section)
  search/     es_client.py index_manager.py (mappings, analyzer, per-fingerprint indices)
  retrieval/  bm25.py vector.py rrf.py rerankers/{base,cross_encoder,jev}.py
              pipeline.py (filter→BM25+vector→RRF→gate→optional rerank→relative filter→dedup→budget)
  generation/ prompts.py (system prompt + guardrails) context_builder.py
              generator.py (stream, retries, inactivity timeout) summarizer.py titles.py
  citations.py  providers/{llm,embeddings}.py  settings_service.py  crypto.py
backend/tests/ ...   backend/eval/ (retrieval eval dataset + script)
frontend/src/
  pages/ ProjectsPage, ProjectWorkspace (tabs: Overview | Files | Chat), SettingsPage
  components/ chat/* explorer/* viewer/{FloatingFileViewer,CodeRenderer,MarkdownRenderer,
              PdfRenderer,DocxRenderer,TableRenderer,TextRenderer} Splash GlobalIngestionIndicator
  api/ (typed client + SSE reader)  lib/ignore.ts (filters using rules fetched from GET /upload-rules)
docker-compose.yml (postgres + elasticsearch)   backend/alembic/ (migrations)
data/ (gitignored: uploaded files under data/projects/{project_id}/..., secret.key)
```

## Data Model (PostgreSQL)

- `projects`: id, name, description, status (`empty|processing|ready|failed|needs_reindex`), embedding_fingerprint, created_at, updated_at, last_ingested_at.
- `files`: id, project_id (FK cascade), file_path (relative, unique per project), file_name, file_type, language, file_size, file_hash, status (`pending|processing|ready|failed`), error_message (internal only), updated_at.
- `chunks`: id, project_id, file_id (FK cascade), ordinal, content, line_start, line_end, page_number, section, token_count. Kept in Postgres so re-indexing can re-embed without re-parsing or re-chunking.
- `conversations`: id, project_id (FK cascade), title, summary, summary_checkpoint_message_id, recent_token_estimate, created_at, updated_at.
- `messages`: id, conversation_id (FK cascade), role, content, sources_json (assistant only, frozen citations), gen_state on user messages (`pending|answered|failed|stopped|dismissed`), created_at.
- `settings`: a single row of JSON config; API keys are stored Fernet-encrypted.

ES documents: `_id = chunk_id`, `{project_id, file_id, file_path, file_type, content, embedding}`.
- **Writes**: Postgres first, then a bulk index into ES with `refresh=wait_for`, and only then is the file marked `ready`. A failed ES write marks the file `failed`, so an unindexed file is never `ready`.
- **Deletes**: a Postgres cascade, plus `delete_by_query` on `file_id`/`project_id` in ES.
- **Consistency**: retrieval always intersects with Postgres `ready` file_ids. Stray ES docs therefore can't leak into answers, and a small `reconcile` command removes orphaned ES docs.

## Backend Behaviour by Feature

**1. Ingestion** (`ingestion/`)
- `POST /projects/{id}/upload` takes a multipart upload of files plus their relative paths. It returns 409 if the project is already ingesting. The rules live in one file, `ingestion/upload_rules.json`, served at `GET /upload-rules`. Server-side filtering repeats the client checks (bad files are skipped and reported per file, not failed as a whole): ignore list (`.git node_modules venv .venv __pycache__ dist build target coverage .env package-lock.json pnpm-lock.yaml`; lock files are machine-generated and accounted for 60 of 536 chunks in the DevDocs eval project), supported extensions, a 20 MB per-file limit and a 50 MB total limit.
- Change detection (`change_detection.py`) is a pure function `diff(old: {path: hash}, new: {path: hash}) -> {created, modified, deleted, unchanged}`. It covers the 5 spec rules; a rename/move counts as delete + create. Hashing streams the file in 1 MB blocks.
- Deleted files: remove the file, its chunks and vectors. Unchanged files: skip. Created or modified files: set to `pending`, then the worker runs parse → chunk → embed (batched, retry on 429) → store. For a modified file, old chunks and vectors are removed before the new ones are written. Each file ends as `ready` or `failed`, and one file failing never fails the project.
- The worker is an in-process asyncio task queue with one job per project (`worker.py`) and keeps running when the user navigates away. Project status is derived from its file statuses.
- `POST /files/{id}/retry`, `PUT /files/{id}` (replace one file; same hash means skip), `DELETE /files/{id}`.

**2. Projects**: CRUD at `/projects`. Rename changes only the name, and delete cascades to every dependent row plus the ES docs (`delete_by_query` on project_id) and the files on disk. `GET /projects/{id}/overview` returns counts, doc/source/other split, language breakdown by extension, status and last ingested time.

**3–4. Explorer**: `GET /projects/{id}/files` returns a flat list that the frontend builds into a tree. `GET /files/{id}/content` streams the raw bytes with the right content-type and returns 404 if the file is gone, which drives the citation message "This file doesn't exist anymore". `GET /ingestion/active` feeds the global indicator.

**5. Retrieval** (`retrieval/pipeline.py`)
1. Eligible chunks are those with `project_id = current` AND file `status = ready`. The backend enforces this; the client is never trusted.
2. The ES BM25 `match` (Top-K 10) and the ES `knn` (Top-K 10, `num_candidates` ~100) run concurrently, both with the `project_id` + ready `file_id` filter.
3. RRF (k=60, internal) fuses the two lists into a single ranking with no duplicate chunks.
4. **Gate:** if the best vector similarity (the top kNN score) is below `0.82`, the question is refused (`NoRelevantContext`, no LLM call). The value is tied to the embedding fingerprint and was chosen from the eval; see [Retrieval.md](Retrieval.md).
5. Optional reranker (off by default, `RERANK_TOP_N`): the `Reranker` interface → `CrossEncoderReranker` or `JevReranker`, chosen by settings, orders the first N candidates.
6. **Relative filter:** keep chunks whose vector similarity is within `0.04` of the best, plus the 2 best BM25 hits. Overlapping line ranges in the same file are cleaned up. Chunks are then added in order until the context token budget (~3k, sized for Groq's 8,000 tokens-per-minute limit) is reached, capped at final Top-K 5.
- If nothing is left, the pipeline returns `NoRelevantContext` and the answer LLM is not called. Infrastructure errors get limited retries, then raise `RetrievalError`.

**6. Answer generation** (`generation/`)
- The context builder combines system prompt (scope guardrail + strict grounding + adaptive Markdown + "label simplified code" rules), the conversation summary, recent messages after the checkpoint, the retrieved chunks (labelled with path and lines), and the question.
- `POST /projects/{pid}/conversations` creates the conversation on the first message, with a heuristic title from the question and no LLM call. `POST /conversations/{id}/messages` sends later messages. Both save the user message first, then stream SSE events: `conversation` → `token`* → `sources` → `done` | `no_context` | `error`.
- The assistant message (content + frozen `sources_json`) is saved only after `done`. If the client disconnects (Stop), the LLM task is cancelled and nothing is saved, and the user message's `gen_state` becomes `stopped`. LLM calls get limited automatic retries before the first token. After the first token, a failure produces `error` and sets the state to `failed`. An inactivity timeout and a generous max-output-token ceiling also apply.
- `POST /messages/{user_msg_id}/retry` runs fresh RAG + LLM without duplicating the user message. `POST /messages/{id}/dismiss` handles Cancel.
- Summarization runs as a background task after a saved answer, triggered when `recent_token_estimate` passes an internal threshold. It folds the older messages into `summary` and moves the checkpoint forward.

**7. Citations** (`citations.py`): a pure function over the final chunks, in retrieval order. It groups chunks by file, keeps each line range as-is, and removes exact duplicates. Code files show "path — Lines a–b, c–d"; non-code files show the path only. Every answer that has context includes sources.

**9. Settings**: `GET/PUT /settings` (keys are returned masked) and `POST /settings/test` (LLM, embedding or Jev ping, with a generic failure message). When the embedding fingerprint changes, projects are marked `needs_reindex` and chat is disabled with a "Re-index" button. `POST /projects/{id}/reindex` re-embeds the Postgres chunks into the new fingerprint's ES index, deletes the project's docs from the old index, and does not re-chunk.

## Frontend

- **Projects page**: cards showing name, file count and last updated, plus create/rename/delete with a confirmation.
- **Workspace header**: project name and status.
- **Overview tab**: stats plus a language bar.
- **Files tab**: collapsible tree, path/filename search (2+ characters, case-insensitive), status badges, and a failed-file popover with Retry/Replace. Upload/Re-upload uses `<input webkitdirectory>` and filters on the client before sending, using the rules fetched from `GET /upload-rules` (no copy of the rules lives in the frontend). The button is disabled while ingestion runs.
- **Floating file viewer**: a modal that picks a renderer by file type. The code renderer scrolls to the first cited range and highlights every cited range.
- **Chat**: sidebar with New Chat (temporary state, nothing persisted) and switch/delete (with confirmation; deleting the open chat opens the most recent remaining one, or the blank state). The composer validates input (non-empty, ≤ 8,000 characters) and is disabled while generating. While waiting it shows rotating cosmetic loading phrases, then streams Markdown. Stop shows "[Stopped] [Retry] [Cancel]", which is rebuilt from `gen_state` when the user returns to the chat. Sources are listed at the end, and clicking one opens the viewer. Chat is disabled when no files are ready, with the spec's exact empty/failed messages and the generic error "Couldn't generate a response. Please try again."
- **Global**: a ~2–3 s logo splash with fade, plus a global ingestion indicator that polls `/ingestion/active` and shows a project-name tooltip.
- **Settings page**: LLM / Embeddings / Reranker (Jev fields appear only when Jev is selected) / Advanced RAG, with Test Connection, Save, and unsaved changes discarded on leave.

## Implementation Phases

Parsing, chunk sizes and the background worker are detailed in [Chunking.md](Chunking.md).

See [Phases.md](Phases.md) for the incremental delivery order. It has 13 phases (0–12), each ending with a demo-able proof.

## Future Enhancements

Not part of V1; recorded so they aren't lost.

- [Future-Caching.md](Future-Caching.md): query-embedding cache, settings cache and an optional exact-match response cache. V1 deliberately has no cache.

## Verification

- **pytest**, using fake LLM/embedding providers so no network calls are made. Integration tests run against the docker-compose Postgres and ES (a test database plus `test-` prefixed indices):
  - change detection: all 5 conditions and the re-ingestion example
  - chunkers: line ranges match the source lines
  - RRF ordering
  - citation formatting: grouping, order, no merging, dedup
  - **project isolation**: two projects that both contain `backend/auth.py` never leak chunks across
  - **READY filter**: processing/failed files are never retrieved
  - no-context: the LLM is never called
  - persistence: stop/failure saves the user message but no assistant message, and retry doesn't duplicate the user message
  - cascades: project delete leaves no Postgres rows and no ES docs; conversation delete keeps chunks
  - analyzer: a BM25 query for `validate_token` and `JwtService` hits the chunks containing those identifiers
- **Retrieval eval**: `python -m eval.run` reports Recall@5 / MRR on the fixture project.
- **End-to-end (manual, `run` skill)**: run `docker compose up -d`, then start uvicorn + vite, create a project, upload a sample folder (including `node_modules` to confirm it is filtered out), and watch the statuses. Then:
  - Modify one file, delete one and add one; re-upload and confirm only those are processed.
  - Ask "How does authentication work?" and confirm the answer streams with line-cited sources and that clicking one highlights the lines.
  - Ask an off-topic question and get the scope refusal; ask an in-scope question with no match and get the fallback.
  - Stop mid-stream, then retry and cancel.
  - Change the embedding model, confirm re-index is required, and re-index.
