# DevDocs V1 — Implementation Phases

This document covers the incremental delivery order. Architecture, stack and data model are in [DevDocs-V1-Implementation-Plan.md](DevDocs-V1-Implementation-Plan.md).

## How the phases work

- Each phase is a **vertical slice**: backend, UI and tests together.
- Each phase ends with a **Proof review**: you run the app (or a script) and see the feature working before the next phase starts.
- Nothing in a later phase is needed to demo an earlier one.

### Dev-only tools
Several proofs use these tools. They are shown only when `DEV_MODE=true`:
- **Chunk Inspector**: lists a file's chunks with their line, page or section metadata.
- **Retrieval Playground**: runs a query and shows each retrieval stage with its scores.
- **ES Inspector**: for the current project, shows the ES doc total and, per file, the Postgres chunk count next to the ES chunk count, so a mismatch (for example after a delete) is visible at a glance. Opened from a dev-only **ES Inspector** button in the Files tab toolbar; layout and behaviour in [ES-Inspector.md](ES-Inspector.md).
- **Context Inspector**: shows the exact prompt sent to the LLM for the last turn.

They make the invisible parts of the pipeline visible, so they can be demonstrated, and they stay available for debugging later.

### Sample projects (used in every demo)
- `samples/ecommerce-demo/`: a small FastAPI + React app with `auth.py`, `jwt.py`, `payment.py`, a README, a PDF architecture doc, a DOCX, YAML/JSON configs, a CSV, a `node_modules/` folder, a `.env` file and a deliberately corrupt PDF.
- `samples/banking-demo/`: also contains a `backend/auth.py` path, with different content. It is used for the project-isolation demos.

---

## Phase 0 — Foundation ✅ Done
**Build**
- `docker-compose.yml` running Postgres 16 and Elasticsearch 8.
- FastAPI app with `/health`.
- SQLAlchemy + Alembic (first migration).
- ES client.
- `.env` configuration.
- Vite + React + TS + Tailwind shell with routing and the logo splash (about 2 s, then fade in).

**Proof**
- `docker compose up -d` brings up both services.
- `GET /health` returns `{postgres: "ok", elasticsearch: "ok"}`.
- Opening the app shows the splash, then an empty Projects page.
- Swagger docs are available at `/docs`.

## Phase 1 — Project Management ✅ Done
**Build**
- `projects` table + CRUD API.
- Projects page: create (name, description), list as cards, rename, delete with a confirmation dialog.
- Project workspace page with Overview / Files / Chat tabs (placeholders).

**Proof**
- Create "E-Commerce API" and "Banking App" and see both cards.
- Rename one; `/docs` shows its `id` is unchanged.
- Delete one after confirming the dialog.
- An empty project shows "No files have been added yet. Upload your project folder to get started." with an **Upload Project** button.
- pytest: CRUD works and renaming keeps the project ID.

## Phase 2 — Folder Upload, Filtering & Document Explorer ✅ Done
**Build**
- Browser folder upload (`webkitdirectory`) with client-side filtering:
  - shared ignore list
  - supported extensions only
  - 20 MB per-file and 50 MB total limits
- The server checks the same rules again.
- Files are stored under `data/projects/{id}/`, keeping their relative paths. The `files` table records path, type, size and language.
- Explorer:
  - collapsible file tree
  - filename/path search (at least 2 characters, case-insensitive, updates while typing)
- Floating read-only viewer with renderers for:
  - code: syntax highlighting + line numbers
  - Markdown
  - PDF with page navigation
  - DOCX
  - CSV as a table
  - plain text
  - HTML shown as source
- Delete an individual file (with confirmation).

**Proof**
- Upload `samples/ecommerce-demo/`. The tree matches the original structure, and `node_modules/` and `.env` are absent.
- Search "jw": `jwt.py` appears with its path, and clicking it opens the viewer.
- Open a `.py`, `.md`, `.pdf`, `.docx`, `.csv` and `.yaml` file; each renders correctly.
- Delete a file and it disappears from the tree.
- No parsing happens yet, so file statuses show "Pending".

## Phase 3 — Change Detection, Re-upload & Project Overview ✅ Done
**Build**
- SHA-256 hashing that reads files in 1 MB blocks.
- A `diff()` change-detection function covering the 5 rules.
- Re-uploading compares the new folder with the stored files.
- Replacing a single file skips processing when the hash is unchanged.
- The upload report lists created, modified, deleted and unchanged files.
- Overview tab: file count, documentation/source/other split, language breakdown bar, last updated.

**Proof**
- Edit `auth.py`, delete `database.py`, add `security.py` and move `utils.py` into a sub-folder, then re-upload. The report shows:
  - modified: `auth.py`
  - deleted: `database.py`, `utils.py`
  - created: `security.py`, `sub/utils.py`
  - everything else unchanged
- The Overview numbers and language bar update.
- pytest: all 5 change-detection conditions plus the spec's re-ingestion example.

## Phase 4 — Parsing, Chunking & Background Ingestion ✅ Done
**Build**
- File-type parsers that all produce one common document format.
- Chunkers, which save chunks in Postgres with line, page or section metadata:
  - Python: split at function/class boundaries using `ast`
  - other code: language-aware splitting
  - Markdown: by heading
  - PDF: by page
  - DOCX: by heading
  - JSON, YAML, CSV, HTML, TXT: one handler each
- In-process background worker that moves each file through Pending → Processing → Ready or Failed. The project status is derived from its files.
- Retry for failed files; replacing a single file uses the same pipeline.
- Upload and re-upload are disabled while ingestion runs.
- Global ingestion indicator with a tooltip naming the project.
- Dev **Chunk Inspector**.
- Embeddings come in Phase 5, so for now "Ready" means parsed and chunked.

**Proof**
- Upload the sample and watch the status badges change live.
- Switch to another project: the global indicator stays visible and ingestion finishes in the background.
- The corrupt PDF shows **Failed**. Its popover reads "DevDocs couldn't process this file" with **[Retry] [Replace File]**. Replacing it with a valid PDF turns it Ready.
- Chunk Inspector:
  - `auth.py` chunks align with function boundaries, and their line ranges match the viewer's line numbers.
  - The README shows section names; the PDF shows page numbers.
- pytest: each chunker's line ranges match the source lines.

## Phase 5 — Embeddings, Elasticsearch Indexing & Project Isolation ✅ Done
**Build**
- Gemini embedding provider (`gemini-embedding-001`, `output_dimensionality=1536`; API key and model set in `.env` for now), with `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` task types, batching, and retry on HTTP 429 rate-limit errors.
- One ES index per embedding fingerprint (provider + model + dimensions), with the code-aware analyzer and a `dense_vector` field (`dims: 1536`).
- Bulk indexing. A file becomes Ready only after it is indexed in ES.
- Deleting a file or project also removes its ES docs.
- A `reconcile` command removes ES docs whose file no longer exists.
- Dev **Retrieval Playground v1**: BM25 Top-10 and vector Top-10 side by side, both filtered by `project_id` and READY files.
- Dev **ES Inspector**: per-file Postgres vs ES chunk counts and the project's ES doc total (backed by a dev-only endpoint that runs ES `_count` grouped by `file_id`).
- **Provider-agnostic rate limiter** (`providers/rate_limit.py`): rolling requests-per-minute and tokens-per-minute windows, one shared limiter per provider key, limits from `.env` (`{GEMINI_EMBEDDING,GEMINI_LLM,GROQ_LLM}_{RPM,TPM}`, 0 = off), and token-aware batching (a batch uses at most 25% of a minute's tokens). Wired into Gemini embeddings now; the other keys are declared and wait for their providers (Phases 7 and 9). The 429 retry with backoff stays as the safety net.

**Proof**
- Query "validate_token": BM25 ranks `jwt.py` first.
- Query "where do we verify the user's identity": vector results surface `auth.py` even though the wording differs.
- **Isolation demo:** upload `banking-demo` and query "authentication" in each project. Each project only returns its own chunks.
- While a file is Processing, it never appears in results.
- **Delete demo:** in the ES Inspector, `auth.py` shows Postgres 6 / ES 6. Delete `auth.py`: its row disappears and the ES total drops by 6 (a `validate_token` search no longer returns it). Delete the whole project: the Inspector shows 0 ES docs for it.
- **Rate-limit demo:** with `GEMINI_EMBEDDING_RPM` set low (for example 20), upload a project of more than 20 chunks. The status badges move on steadily but slower, no file turns Failed, and the backend log shows `rate limiter: waiting …s` lines instead of HTTP 429 errors.
- pytest: project isolation, the READY filter, and ES `_count` for a deleted file going from N to 0 without touching other files or projects; limiter window, batch splitting and 429 retry (fake clock, mock transport).

## Phase 6 — Hybrid Retrieval Pipeline + Evaluation ✅ Done
**Build**
- RRF fusion of the BM25 and vector results.
- A refusal **gate** on the best vector similarity (default 0.82), and a **relative filter** that drops chunks far below the best one while always keeping the 2 best BM25 hits.
- `Reranker` interface with `CrossEncoderReranker` (fastembed, runs locally) as an **optional** stage, off by default (`RERANK_TOP_N=0`); the data showed the gate and filter beat it on accuracy and speed (see `Docs/Reports/Retrieval-Ablation.md`).
- Overlap cleanup, a ~3k-token context budget (fits Groq's 8,000 tokens-per-minute limit) and a final Top-K of 5.
- A `NoRelevantContext` result (with the reason) when nothing relevant remains.
- Limited retries when search infrastructure fails.
- Retrieval eval harness in `backend/eval/`: a question → expected-files dataset, reporting hit rate, Recall@5, MRR and off-topic refusal; a frozen held-out set (`eval.holdout`) with measured end-to-end latency.

**Proof**
- Retrieval Playground v2 shows every stage for a query: BM25 → vector → RRF ranking → gate decision (top vector similarity vs threshold) → relative filter → final context with its token count, plus why each dropped chunk was dropped.
- An off-topic query ("best beaches in Goa") returns **No relevant context**.
- `python -m eval.run` prints a metrics table and the gate threshold selection; `python -m eval.holdout` reports the frozen-set comparison with the previous pipeline.

## Phase 7 — Chat with Streaming RAG Answers
**Build**
- `conversations` and `messages` tables.
- New Chat starts as a temporary blank state. The first message creates the conversation and gives it a title derived from the question.
- Message validation: no empty messages, maximum 8,000 characters.
- System prompt with the scope guardrail and strict-grounding rules.
- Context builder: system prompt + recent messages + retrieved context + question.
- **Chat LLM rate limiting:** the `LLMProvider` wrapper uses the shared limiter (`get_limiter("groq_llm")` or `get_limiter("gemini_llm")`, set by `LLM_PROVIDER`) and calls `acquire(requests=1, tokens=estimate)` before each call, where the estimate is prompt tokens + max completion tokens (a stream's real cost is unknown up front). `GROQ_LLM_TPM` defaults to 7000 for the key's 8,000 tokens-per-minute limit.
- SSE streaming from the chat LLM chosen by `LLM_PROVIDER` in `.env` (`groq` default: `openai/gpt-oss-120b`; or `gemini`; model names and API keys from `.env` for now; only final content tokens are streamed, not reasoning tokens), rendered as Markdown with code blocks.
- The user message is saved immediately; the assistant message is saved only once it completes.
- No-context fallback that skips the LLM call.
- Rotating cosmetic loading phrases until the first token arrives.
- Chat is disabled while no files are Ready, using the spec's messages.

**Proof**
- Ask "How does authentication work?": loading phrases rotate, then a grounded answer streams in.
- Ask "Where is the payment retry mechanism?" (not in the sample): the reply is "I couldn't find relevant information in this project…", and the backend log shows the LLM was not called.
- Ask "Plan a Kerala trip": DevDocs declines because it's out of scope.
- Refresh the page: the conversation is restored from the database with no LLM call.
- In a project whose only file failed, chat is disabled and shows the failure message.
- **Rate-limit demo:** ask two long questions back to back with `GROQ_LLM_TPM` set low. The second waits (the loading phrases keep rotating, the backend log shows `rate limiter: waiting …s`) instead of failing with a 429 error.

## Phase 8 — Citations & Source Navigation
**Build**
- Application-side citation builder:
  - keeps retrieval order
  - groups chunks from the same file
  - keeps each line range separate (no merging)
  - removes exact duplicates
  - code files show line ranges; other files show the path only
- Sources are sent as an SSE event and saved with the answer in `messages.sources_json`.
- Clicking a citation opens the floating viewer, scrolls to the first range and highlights all cited ranges.
- If the file has been deleted, clicking shows "This file doesn't exist anymore."

**Proof**
- Every answer ends with a Sources list, for example `backend/auth.py — Lines 10–20, 35–48` and `README.md`.
- Clicking a citation opens the viewer with the cited lines highlighted; the chat stays visible behind it.
- Delete `auth.py` in the Explorer, then click its citation in an old answer: the deleted-file message appears and the answer is unchanged.
- pytest: citation formatting rules.

## Phase 9 — Conversation Management & Context Memory
**Build**
- Summary and title LLM calls go through the same `LLMProvider` wrapper, so they draw from the same shared rate-limit window as chat answers (no separate wiring).
- Conversation sidebar: list, switch, and delete with confirmation.
- Deleting the open chat opens the most recently updated remaining conversation, or the blank state if none are left.
- Conversation summary with a checkpoint and a running token estimate. Summarization runs in the background once the estimate passes a threshold.
- Dev **Context Inspector**.

**Proof**
- Create three conversations, switch between them, then delete the open one; the correct next chat opens.
- Deleting a conversation leaves the project's files and chunks untouched in the Explorer and Playground.
- In a long conversation (with a lowered threshold), the Context Inspector shows a summary plus only the recent messages.
- A follow-up like "where is that token generated?" still resolves correctly.

## Phase 10 — Interruption, Errors & Resilience States
**Build**
- **Stop**: the client aborts and the server cancels the LLM call. The partial answer stays visible but is not saved, with **[Stopped] [Retry] [Cancel]** shown.
- **Retry**: reuses the same user message (no duplicate) and runs retrieval and the LLM again from scratch.
- **Cancel**: clears the stopped state; the user message stays.
- On returning to the chat, the stopped state is rebuilt from `gen_state`. Sending a new query abandons the stopped one.
- Limited automatic retries for LLM and retrieval calls, an inactivity timeout, and a generous output-token ceiling.
- Generic error: "Couldn't generate a response. Please try again." + Retry.
- Dev fault-injection flags (`FAULT_LLM`, `FAULT_RETRIEVAL`) to show the failure paths.

**Proof**
- Stop mid-stream: the partial answer shows with the Stopped indicator.
- Refresh: the partial answer is gone and the buttons are back.
- Click Retry: a fresh answer streams, and the database has no duplicate user message.
- With `FAULT_LLM=always`, the generic error appears after retries are exhausted, and the user message stays saved.
- With `FAULT_LLM=once`, the answer succeeds after one silent automatic retry.
- Provider error text never appears in the UI.

## Phase 11 — Settings, Re-indexing & Jev Adapter
**Build**
- Re-indexing re-embeds stored chunks through the embedding provider, so it is paced by the same shared limiter. The Settings UI later replaces the `.env` rate-limit values.
- Settings page with four sections:
  - LLM
  - Embeddings
  - Reranker: Cross-Encoder by default; Jev fields appear when Jev is selected
  - Advanced RAG
- API keys are stored encrypted and shown masked. The visibility toggle only works for keys you have just typed.
- Test Connection and Save. Unsaved changes are discarded when you leave the page.
- LLM, reranker and Advanced RAG changes apply from the next query.
- Changing the embedding settings marks projects `needs_reindex` and disables their chat until re-indexed. **Re-index** re-embeds the stored chunks into a new ES index.
- `JevReranker` adapter, finished once Jev's API details are known.

**Proof**
- A wrong API key: Test Connection shows "Couldn't connect…". The correct key connects.
- Change the temperature or model: the next answer uses it, as the Context Inspector shows.
- Change BM25 Top-K: the Playground reflects it.
- Change the embedding model:
  - the project shows "Re-index required"
  - Re-index runs without a re-upload
  - the Chunk Inspector shows the same chunk IDs as before
  - chat works again

## Phase 12 — Hardening & V1 Acceptance
**Build**
- Tune the threshold and context budgets using the eval.
- Complete the pytest suite.
- Clean up the UI and polish the empty and loading states.
- README with setup steps: `docker compose up`, backend and frontend run commands, and an example `.env`.

**Proof**
- The full V1 acceptance walkthrough (the end-to-end checklist in the main plan's Verification section) passes from a fresh clone.
- pytest is green and the eval metrics report is produced.

---

## Phase → Spec Coverage

| Spec (`Docs/Features/`) | Phases |
|---|---|
| 1 Document Ingestion | 2, 3, 4, 5 |
| 2 Project / Workspace Management | 1, 3, 5 |
| 3 Chat Interface | 7, 9 |
| 4 Document Explorer | 2, 4 |
| 5 Search & Retrieval | 5, 6 |
| 6 RAG Answer Generation + Guardrails | 7, 9, 10 |
| 7 Citations & Source Display | 8 |
| 8 Error / Loading / Empty States | 1, 4, 7, 10, 12 |
| 9 Settings & Configuration | 11 |
