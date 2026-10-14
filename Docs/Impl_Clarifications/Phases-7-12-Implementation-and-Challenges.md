# Phases 7–12: implementation, challenges and solutions

Written for: a developer or reviewer who wants to know how chat, citations, memory, resilience, settings and the optimisation work were built, what went wrong, and how each problem was solved. Date: 2026-10-14. Measured results are in [Retrieval-Optimisation-Round-2.md](../Reports/Retrieval-Optimisation-Round-2.md); file order and flows are in [Backend-File-Flow.md](../Backend-File-Flow.md).

## 1. What was built

| Phase | Feature | Main files |
|---|---|---|
| 7 | Streaming chat (SSE), no-context fallback that skips the LLM, rate-limited LLM wrapper | `providers/llm.py`, `generation/chat.py`, `generation/context_builder.py`, `api/conversations.py` |
| 8 | Citations built in code, frozen in `messages.sources_json`, clickable with line highlight | `generation/citations.py`, `CodeRenderer.tsx` |
| 9 | Conversation sidebar, delete, background summary with checkpoint, dev Context Inspector | `generation/memory.py`, `generation/inspector.py`, migration 0006 |
| 10 | Stop / Retry / Cancel, silent retry, inactivity timeout, fault injection | `chat.py`, `conversations.py` (`retry`, `dismiss`) |
| 11 | Settings (4 sections), encrypted keys, Test Connection, re-index, Jev settings | `settings_store.py`, `api/settings.py`, `ingestion/reindex.py` |
| 12 | README, `.env.example`, docs and diagrams synced | |
| — | Retrieval round 2, answer-phase optimisation, UI redesign | `retrieval/query.py`, `fusion.py`, `bm25.py`, `rate_limit.py`, `llm.py`, `frontend/` |

## 2. How a chat turn works

1. The API saves the user message (`gen_state=pending`) and streams SSE events: `conversation` → [`waiting`] → `token`* → `sources` → `done` | `no_context` | `error`.
2. Memory (summary + messages after the checkpoint) is loaded; a follow-up gets the previous question added to the search text.
3. Retrieval runs fresh for every question. No relevant context → fixed reply, LLM never called.
4. Citations and the prompt are built; the LLM streams through a rate-limited, routed provider.
5. Only after the stream completes are the assistant message and `answered` saved, in one transaction. Failure records `failed`; a client disconnect records `stopped`; neither saves partial text.

## 3. Challenges and solutions

### 3.1 Stop recorded `failed` instead of `stopped`
- **Symptom:** in a live run, disconnecting mid-wait logged "unexpected error … aclose(): asynchronous generator is already running" and the question ended `failed`.
- **Cause:** the inactivity timeout wrapped `__anext__()` of the provider's async generator in `asyncio.wait_for`. Cancelling that await leaves the generator mid-flight; my cleanup then tried to close it, raised `RuntimeError`, and that replaced the `CancelledError`.
- **Fix:** a producer task pushes tokens into a queue and the consumer waits on the queue with a timeout. Waiting on a queue is safe to cancel, and cancelling the producer closes the provider stream cleanly.
- **Why tests missed it:** the existing test closed the generator politely with `aclose()`. A new test cancels a turn parked inside the LLM call, and the fix was re-verified live (`[('user','stopped')]`, no assistant row).

### 3.2 Back-to-back questions waited 43 seconds
- **Cause:** Groq's free key allows 8,000 tokens/minute per model. A question costs ~2,500–3,500 tokens, and the old code reserved prompt + 1,500 output tokens and never returned the unused part.
- **Solutions, in order of effect:**
  1. **Settle:** request `stream_options.include_usage`; when the stream ends, correct the reservation to the real total (fallback estimate if no usage arrives).
  2. **Announce:** `wait_estimate()` lets chat send a `waiting` event with the seconds, so the UI says why it pauses.
  3. **Route:** limits are per model, so `RoutingLLMProvider` sends a call to `gpt-oss-20b` (own limiter `groq_llm_fallback`) only when the primary would wait more than 6 s and the fallback would wait less. It stays on Groq.
- **Result:** third question in a minute 43 s → 1.6–2.0 s. Six questions inside one minute still wait; the UI says so.
- **Not measured:** answer quality of 20b vs 120b; the fallback is switchable off.

### 3.3 Test files crowded out the implementation
- **Symptom:** asking how the relative filter works served three test chunks and a migration; the real `relative_filter` chunk was cut by Top-K.
- **Fix:** multiply the RRF score of test-file chunks by 0.8 unless the question contains "test".
- **Evidence:** MRR +3.5 / +6.9 pts, hit +1.2 / +1.4 pts on the tuning / held-out sets.

### 3.4 BM25 could not match file names
- **Cause:** `file_path` was a `keyword` field, so "what does auth.py do" had nothing to match, and long natural-language questions diluted BM25.
- **Fix:** new `path_text` and `section` text fields (same code-aware analyzer), a stopword-stripped query over `content`, `path_text` and `section`. The strict mapping needed a `put_mapping` on existing indexes and a backfill script (`app.search.backfill`) that fills the fields from Postgres without embedding anything.

### 3.5 Ideas that measured worse
Recorded so they are not retried blindly: file-level score boosts (hit −5 to −24 pts), 20 candidates per leg (hit −2 / −7 pts), weighted RRF (±1 pt), a lexical rescue just under the gate (+2 answered, +1 leak), and contextual embedding headers (MRR up, Recall@5 and refusal slightly down, full re-embed). The experiments are scripts in `backend/eval/` that use cached embeddings, so they cost no API quota.

### 3.6 Follow-up questions had no retrieval context
- **Problem:** retrieval is fresh per question, so "where is that token generated?" embeds poorly.
- **Fix:** add the previous user question to the search text, but only when the question contains a reference word (it, that, this …). Using shortness as the trigger was rejected: "best beaches in Goa" is short, would borrow the previous topic and slip past the relevance gate.

### 3.7 Query embedding dominated latency
- **Cause:** a new HTTP client (TLS handshake) per question; repeated questions re-called the API.
- **Fix:** keep-alive client bound to the running event loop (recreated if the loop changes, which matters for tests) and a 256-entry LRU. Warm retrieval 1.2 s → 0.5–0.8 s; repeats 30–70 ms.
- **Related:** `localhost` cost ~0.2 s per connection on Windows (IPv6 first, server on IPv4), so the frontend defaults to `127.0.0.1`.

### 3.8 Embedding quota burned on every edit
- **Cause:** modifying a file re-embedded all its chunks; the Gemini free tier is ~1,000 calls/day.
- **Fix:** before deleting a file's old docs, read `{sha256(content): vector}`; embed only chunk texts not present. Re-index also skips chunk ids already in the new index, so an interrupted run resumes.

### 3.9 `needs_reindex` was erased by status derivation
- **Cause:** project status is derived from file statuses and never produced `needs_reindex`, so any file operation would silently clear it.
- **Fix:** `refresh_project_status` keeps `needs_reindex` unless the project becomes empty; only a completed re-index clears it. Upload, replace and retry answer 409 while it is set, because the worker would otherwise mix two fingerprints in one project.

### 3.10 Settings had to apply without a restart
- **Problem:** the app reads a global `settings` object in many places and caches providers.
- **Fix:** saved values (Postgres row, keys Fernet-encrypted with `data/secret.key`) are applied onto that object and the LLM, embedding and reranker caches are reset, so the next question uses them. A blank key field means "keep the stored key" so a masked, untouched field cannot wipe it. Test Connection accepts unsaved form values and always returns the same generic failure text.

### 3.11 Tests leaked configuration and hit the network
- **Problems:** settings tests mutate the shared `settings`; one Test Connection test reached the real Groq API because `.env` keys were loaded.
- **Fix:** an autouse fixture snapshots and restores `settings` and points `data_dir` at a temp folder (so `secret.key` never lands in `data/`); the connection test blanks the key so it fails before any request. Re-index uses the injected session factory so it targets the test database.

### 3.12 A flaky assertion
`"500" not in response` failed when a random UUID contained "500". The check stays, applied after replacing UUIDs with a placeholder.

### 3.13 Migrations vs `create_all`
Tests build tables with `create_all`, so a missing Alembic migration is invisible to them: the first live request failed with "column summary does not exist". Migration 0006 was added and applied; remember to run `alembic upgrade head` after pulling.

### 3.14 Environment problems
- **Docker Desktop hung** ("unable to start") mid-session. Killing Docker processes, `wsl --shutdown` and relaunching recovered it with volumes intact.
- **Shell heredocs mangled backslashes** (`\n` became a real newline; some heredocs failed to parse). Source edits switched to the Write/Edit tools, and `compileall` / `npm run build` were used to verify. Noted in CLAUDE.md.

### 3.15 Frontend
- **Bundle:** entry chunk 447 KB → 73 KB gzipped by lazy-loading pages and the file viewer (the viewer chunk is still 813 KB because it holds pdf.js and mammoth).
- **Focus ring:** a global `:focus-visible` rule overrode Tailwind's `outline-none` and produced a double ring on the composer; moved into `@layer base`.
- **Streaming UI:** Markdown is memoised so only the growing last message re-renders; the partial answer stays visible after Stop (not saved) with [Stopped] [Retry] [Cancel], rebuilt from `gen_state` after a refresh.

## 4. Limits and open items

- The held-out question set was used to select variants, so it is no longer an untouched test; all questions were written by one person for one project.
- Latency figures are single live runs on one machine.
- `JevReranker` is a stub until Jev's API details exist.
- A daily Gemini quota 429 during ingestion still marks the remaining files `failed` (Retry works).
- No automated answer-quality eval; no manual acceptance walkthrough on a fresh clone.
