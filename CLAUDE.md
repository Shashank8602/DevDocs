# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project status

Implementation follows the phases in `Docs/Plan/Phases.md`. **All phases 0–12 are implemented** (Phases 0–6 approved by the user; Phases 7–12 delivered in one pass on the user's instruction "take over and fully implement all the remaining features", so they have **not had the per-phase proof review**: run the proofs in `Phases.md` to accept them). Also done in that pass: retrieval round 2 (BM25 over path/section, test-file prior, follow-up resolution), the answer-phase work (token settling, same-provider Groq fallback model, wait notice), vector reuse on file edits, and a UI redesign. See `Docs/Reports/Retrieval-Optimisation-Round-2.md` and `Docs/Reports/Optimisation-Analysis.md` (what is next). Open: `JevReranker` is a stub until Jev's API details are provided; a daily Gemini embedding quota 429 still marks remaining files `failed` (Retry works). Providers: Groq is the chat default (`LLM_PROVIDER=groq`; Gemini chat is switchable but slower), Gemini embeddings. Runtime settings (Settings page) are stored in Postgres and override `.env`. The repo is a git repo with one initial commit; the phase 7–12 work is uncommitted.

## Commands

```
cp .env.example .env                      # first time
docker compose up -d                      # Postgres 16 + Elasticsearch 8
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd backend && .venv/Scripts/alembic upgrade head
cd backend && .venv/Scripts/uvicorn app.main:app --reload   # :8000, /health, /docs
cd backend && .venv/Scripts/python -m pytest
cd frontend && npm install && npm run dev                   # :5173
cd frontend && npm run build                                # tsc + vite build
cd backend && .venv/Scripts/python -m eval.run              # retrieval eval (Recall@5 / MRR); needs ES + Postgres
cd backend && .venv/Scripts/python -m app.search.reconcile  # remove ES docs whose file is gone from Postgres
cd backend && .venv/Scripts/python -m app.search.backfill   # fill path_text/section on an index created before round 2 (no API calls)
cd backend && .venv/Scripts/python -m eval.bm25_variants    # offline retrieval-variant comparison (cached embeddings, no API calls)
backend/.venv/Scripts/python scripts/check_providers.py       # (repo root) verify the Groq / Gemini keys in .env
```

## What DevDocs is

A project-scoped RAG assistant for software projects. The user uploads a project folder; DevDocs ingests it incrementally (SHA-256 change detection) and the user chats with it to understand the code, with deterministic, clickable citations (file path + line ranges).

## Where things live

| Path | Contents |
|---|---|
| `Docs/Features/` | 13 spec PDFs, grouped by feature (1 Ingestion … 9 Settings). Source of truth for V1 behaviour. |
| `Docs/Plan/DevDocs-V1-Implementation-Plan.md` | Architecture, stack, data model, per-feature backend behaviour, verification. |
| `Docs/Plan/Phases.md` | 13 phases (0–12), each a vertical slice ending in a demo-able proof. |
| `Docs/Plan/Future-Caching.md` | Post-V1 caching analysis (not in V1 scope; V1 has no cache). |
| `Docs/Backend-File-Flow.md` | Backend file sequence numbers, dependency table/graph, migration chain, runtime flows. Source of truth for file order. |
| `Docs/Plan/Chunking.md`, `Retrieval.md`, `ES-Inspector.md` | Per-topic design docs for Phases 4–6 (parsing/chunking, hybrid retrieval and eval, dev ES Inspector). |
| `Docs/Plan/Diagrams/` | 14 Mermaid diagrams + `README.md` index. |
| `Docs/Reports/`, `Docs/Impl_Clarifications/` | Measured results and open weak spots (retrieval); short implementation notes (hashing, reranker threshold). |
| `Docs/Running-with-Docker.md` | Running the stack in Docker. |

Layout: `backend/` (FastAPI in `app/`, Alembic in `alembic/`, retrieval eval in `eval/`, tests in `tests/`), `frontend/` (Vite + React + TS), `scripts/` (standalone tools, e.g. `check_providers.py`), `docker-compose.yml`, `data/` (gitignored uploads + `secret.key`), `samples/` (demo projects). Full tree is in the implementation plan.

## Stack decisions (user's choices — do not override)

- **Postgres 16** (source of truth) + **Elasticsearch 8.x** for *both* BM25 and dense-vector kNN. **Do not propose ChromaDB or SQLite-FTS.**
- Local single-user app, no auth. FastAPI + SQLAlchemy 2 async + Alembic; React + Vite + Tailwind + TanStack Query.
- **Groq** for the LLM by default (`openai/gpt-oss-120b`, via the `openai` SDK pointed at Groq's OpenAI-compatible base URL), switchable to **Gemini** chat with `LLM_PROVIDER=groq|gemini` in `.env` (Settings UI later), and **Google Gemini** for embeddings (`gemini-embedding-001`, `output_dimensionality=1536`, cosine; `RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY` task types) behind thin `LLMProvider`/`EmbeddingProvider` wrappers — nothing else imports these SDKs directly (`EmbeddingProvider` calls Gemini over REST with `httpx`; `LLMProvider` uses the `openai` SDK for Groq and Gemini, with a same-provider router for the fallback model). Switched from OpenAI on 2026-10-10. Infra is ₹0; API calls are metered and rate-limited.
- Reranker behind a `Reranker` interface and **optional, off by default** (`RERANK_TOP_N=0`; the user approved dropping it from the default path after the data showed the vector-similarity gate and filter beat it): local fastembed Cross-Encoder when on; `JevReranker` ("System One" model) is an adapter to finish once its API details are provided.
- RRF (k=60) is done in application code, not ES.

## Key design invariants

- Retrieval must always filter by `project_id` **and** Postgres `ready` file IDs (project isolation; never trust the client).
- A file becomes `ready` only after its chunks are indexed in ES. Postgres writes first, then ES.
- ES index is per embedding fingerprint (`devdocs-chunks-{fingerprint}`); changing embedding config marks projects `needs_reindex` and re-embeds stored chunks without re-chunking.
- No relevant context (the question fails the vector-similarity gate, or nothing survives the filter) → return `NoRelevantContext` and **do not call the LLM**.
- Assistant message is saved only after the stream completes; Stop saves nothing and sets user message `gen_state=stopped`.
- Citations are built in application code (not by the LLM): retrieval order, grouped by file, line ranges kept separate, exact duplicates removed; non-code files show path only.
- Where specs conflict, the later-dated spec wins (see the conflict table in the plan).

## Working conventions

- **Keep docs modular:** one topic per `.md` under `Docs/Plan/`, linked from the main plan. Don't fold topics into one large document.
- **Keep diagrams in sync:** when the plan changes, update the matching file in `Docs/Plan/Diagrams/` in the same edit.
- **Backend file order is a rule:** every backend file has a sequence number in `Docs/Backend-File-Flow.md`. A file's number must be higher than every file it imports (parent first, child after). Never renumber or reuse a number. New files take the next free number and go in the matching feature section. Before creating a backend file, list its imports and check they already have lower numbers. When a feature adds or changes backend files, migrations, or runtime flows, update that document in the same edit (table, "What each file does", migrations table, flows). **The Mermaid dependency graph in that document must be updated after every feature is implemented**: add a node for each new file, draw an edge from each imported file to the importer, and remove edges that no longer exist. Do this before reporting the feature as done. Migrations chain via `down_revision`; a new one points at the latest revision.
- **Deliver phase by phase:** implement one phase at a time, and stop at the phase boundary to show the proof described in `Phases.md` for the user to review before continuing.
- Dev-only tools (Chunk Inspector, ES Inspector with vector peek, Retrieval Playground, Context Inspector, `FAULT_LLM` / `FAULT_RETRIEVAL` flags) are gated by `DEV_MODE=true`.
- **Do not edit files with `python - <<EOF` heredocs that contain backslashes or quotes in this environment**: escapes get halved (`
` became a real newline). Use the Write / Edit tools.
- Tests use fake LLM/embedding providers (no network); integration tests run against docker-compose Postgres + ES. Retrieval quality is measured with `python -m eval.run` (Recall@5 / MRR).
- Environment is Windows (PowerShell/Git Bash); use scratchpad for temp files.
