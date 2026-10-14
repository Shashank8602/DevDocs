# DevDocs

A project-scoped RAG assistant for software projects. Upload a project folder, let DevDocs ingest it incrementally (SHA-256 change detection), and chat with it to understand the code. Answers are grounded in your files and carry deterministic, clickable citations (file path + line ranges).

- **Search:** Elasticsearch BM25 + dense-vector kNN, fused with RRF, then a relevance gate that refuses off-topic questions without calling the LLM.
- **Chat:** streamed answers from Groq (`openai/gpt-oss-120b` by default), Markdown with code blocks, Stop / Retry / Cancel, conversation memory.
- **Free-tier friendly:** client-side rate limiting, token accounting, a same-provider fallback model, vector reuse when a file changes.
- **Local and single-user:** Postgres 16 + Elasticsearch 8 in Docker, FastAPI backend, React frontend. No auth.

## Quick start

Requirements: Docker Desktop, Python 3.11+, Node 18+, a [Groq](https://console.groq.com) key (chat) and a [Gemini](https://aistudio.google.com) key (embeddings).

```bash
cp .env.example .env            # then set GROQ_API_KEY and GEMINI_API_KEY
docker compose up -d            # Postgres 16 + Elasticsearch 8

cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows; use .venv/bin/pip on macOS / Linux
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn app.main:app --reload         # http://127.0.0.1:8000  (/health, /docs)

cd ../frontend
npm install
npm run dev                                          # http://localhost:5173
```

Then: create a project, open **Files**, upload a folder (try `samples/ecommerce-demo/`), wait for the files to turn **Ready**, and open **Chat**.

Windows PowerShell step-by-step commands: [Docs/Running-with-Docker.md](Docs/Running-with-Docker.md).

## Configuration

Defaults come from `.env` (see `.env.example`); the **Settings** page overrides them at runtime and stores API keys encrypted (`data/secret.key`, created on first use; keep it out of version control).

| Area | Where |
|---|---|
| Chat model, temperature, reasoning effort, fallback model, keys | Settings → LLM |
| Embedding model and dimensions (changing them requires **Re-index**) | Settings → Embeddings |
| Optional reranker (off by default) | Settings → Reranker |
| Gate, filter, Top-K, token budget | Settings → Advanced RAG |

Groq is the default chat provider (`LLM_PROVIDER=groq`). Embeddings always use Gemini.

## Everyday commands

```bash
cd backend && .venv/Scripts/python -m pytest            # offline tests (fake LLM / embeddings); needs docker compose up
cd backend && .venv/Scripts/python -m eval.run          # retrieval eval (Recall@5 / MRR); needs ES + Postgres + Gemini key
cd backend && .venv/Scripts/python -m app.search.reconcile   # remove ES docs whose file is gone
cd backend && .venv/Scripts/python -m app.search.backfill    # fill path/section fields on an older index (no API calls)
cd frontend && npm run build                            # type-check + production build
python scripts/check_providers.py                       # (repo root) verify the Groq / Gemini keys
```

Set `DEV_MODE=true` to show the dev tools: Chunk Inspector, ES Inspector (with vector peek), Retrieval Playground and the chat **Context** inspector; `FAULT_LLM` / `FAULT_RETRIEVAL` (`once` | `always`) inject failures to demonstrate the error paths.

## Documentation

| | |
|---|---|
| [Docs/Plan/DevDocs-V1-Implementation-Plan.md](Docs/Plan/DevDocs-V1-Implementation-Plan.md) | architecture, stack, data model, behaviour per feature |
| [Docs/Plan/Phases.md](Docs/Plan/Phases.md) | delivery phases and their proofs |
| [Docs/Backend-File-Flow.md](Docs/Backend-File-Flow.md) | every backend file, its order and dependencies, runtime flows |
| [Docs/Plan/Retrieval.md](Docs/Plan/Retrieval.md) | the retrieval pipeline and how it was tuned |
| [Docs/Reports/Optimisation-Analysis.md](Docs/Reports/Optimisation-Analysis.md) | what was optimised, what is next |
| [Docs/Plan/Diagrams/](Docs/Plan/Diagrams/README.md) | Mermaid diagrams |

## Troubleshooting

- **"No files are ready yet"**: ingestion runs in the background; watch the status badges in Files. A failed file can be retried or replaced.
- **A question waits before answering**: Groq's free key allows 8,000 tokens per minute per model; the chat says how long. The fallback model absorbs bursts.
- **"Re-index required"**: the embedding settings changed. Use the banner's **Re-index** button; no re-upload is needed.
- **429 while embedding**: the Gemini free tier allows about 1,000 embedding calls per day. Retry the failed files later.
