# Optimisation analysis (V1 complete)

Written for: the project owner, as the requested review of how DevDocs can be made faster, cheaper and more accurate now that V1 is implemented. Date: 2026-10-14. "Done" means implemented and tested in this repository; "Next" means recommended but not built; numbers come from [Retrieval-Optimisation-Round-2.md](Retrieval-Optimisation-Round-2.md) and live runs on this machine.

The constraint that shapes almost everything is the free tiers: Groq allows 8,000 tokens per minute per model, Gemini embeddings about 1,000 calls per day. The cheapest optimisation is therefore usually "spend fewer tokens and calls", not "run faster".

## 1. Retrieval quality

| Area | Status | Notes |
|---|---|---|
| BM25 over path and heading, question words stripped | **Done** | share +4 to +5 pts, BM25 pool +9 to +13 pts |
| Test-file prior ×0.8 | **Done** | MRR +3.5 / +6.9 pts; a setting, off at 1.0 |
| Follow-up resolution (previous question prepended on reference words) | **Done** | no extra LLM call; shortness alone never triggers it, so an unrelated short question cannot borrow a topic past the gate |
| Gate 0.82, filter 0.04 / protect 2, RRF k=60, reranker off | Unchanged | re-measured: still the best structure found; the gate band is the limit (0.81 → 0.83 swings hit by 4 to 10 pts) |
| Contextual chunk headers in the embedded text | Not shipped | mixed result (MRR up, Recall@5 and refusal slightly down) for a full re-embed; revisit only with more questions |
| Per-project gate calibration | **Next** | the gate is tied to the embedding model and was validated on one project. A "calibrate" action that embeds ~30 generated off-topic questions and picks the threshold from the project's own similarity distribution would remove the main accuracy risk on new projects |
| Real questions | **Next** | every number comes from questions written by the pipeline's author. Chat now exists: log (question, served files, thumbs up/down) locally and grow the eval set from real use |
| Code-aware reranker | Parked | too slow on this CPU (5 to 16 s); the `Reranker` interface and Jev settings are ready for a hosted one |
| Neighbour expansion (add the chunk before/after a hit) | **Next, to test** | cheap to try offline with the existing harness; likely helps "how does X work" questions that span chunk boundaries |

## 2. Answer (LLM) phase

| Area | Status | Notes |
|---|---|---|
| Token reservation settled to real usage | **Done** | frees budget the answer did not spend |
| Same-provider fallback model with its own budget | **Done** | 43 s → 1.6 to 2.0 s for the third question in a minute; quality of 20b vs 120b not measured |
| Visible rate-limit wait (`waiting` event) | **Done** | |
| Silent retry before the first token, inactivity timeout, Stop cancels cleanly | **Done** | found and fixed a real bug: cancelling while waiting on the LLM recorded `failed` instead of `stopped` |
| Prompt compaction (trailing whitespace, blank runs) | **Done** | small, safe |
| Conversation summary instead of full history | **Done** | background call, same limiter |
| First-token latency | Model-bound | 1.5 to 3.5 s on gpt-oss-120b (reasoning), ~1 s on gpt-oss-20b. Settings → LLM lets you pick; `reasoning_effort=low` is already the minimum |
| Answer-length control by question type | **Next** | output tokens are the largest per-question cost; a one-line instruction for "where is X?" style questions would cut tokens and wait |
| Trim the system prompt (~450 tokens, paid on every call) | **Next** | Groq caches an identical prefix, so the gain is smaller than it looks; measure before cutting guardrail wording |
| Adaptive context budget (fewer chunks when the top chunk is far ahead) | **Next, to test** | the filter is near its limit; a smaller budget for confident questions would save tokens but risks recall |
| Answer-quality eval (faithfulness, citation correctness) | **Next** | there is none; retrieval metrics do not say whether the answer is right |

## 3. Ingestion and storage

| Area | Status | Notes |
|---|---|---|
| Reuse vectors of unchanged chunks when a file is modified | **Done** | spends quota only on new or changed text |
| Index replicas set to 0 | **Done** | single local node: the replica could never be assigned |
| Re-index resumes and skips chunks already in the new index | **Done** | an interrupted run does not pay twice |
| Daily embedding quota hit during ingestion | **Next** | remaining files are marked `failed`; they should stay `pending` and the job should pause until quota returns (Retry works today) |
| Embed several files concurrently | **Next** | files are processed one at a time; the limiter already paces total calls, so this mostly helps Postgres/ES write latency |
| Skip generated and minified files by content | **Next** | the ignore list is name-based; lock files were the largest source of useless chunks |
| Backfill for an index created before `path_text` / `section` | **Done** | `python -m app.search.backfill`, no embedding calls |

## 4. Application

| Area | Status | Notes |
|---|---|---|
| Keep-alive HTTP connection and LRU for query embeddings | **Done** | 1.1–1.3 s → 0.5–0.8 s warm; 30–70 ms repeated |
| Frontend loads a 73 KB (gzip) entry chunk instead of 447 KB | **Done** | pages and the file viewer are lazy |
| Frontend talks to `127.0.0.1` | **Done** | `localhost` cost ~0.2 s per new connection on Windows |
| File viewer chunk is 813 KB (PDF + DOCX renderers) | **Next** | split per renderer so a code file does not load pdf.js |
| Long conversations | **Next** | the whole message list is rendered and re-read from Postgres each turn; fine for tens of messages, virtualise and read only after the checkpoint beyond that |
| Per-stage latency in the dev inspector | **Next** | the log already prints retrieval / first token / total per turn; surface it in the Context Inspector |

## 5. What will not help

- A second embedding provider or a local embedding model: the gate and every threshold are tied to `gemini-embedding-001` at 1,536 dims; changing it means re-embedding and re-tuning, for a latency gain of at most the 0.5 s embedding call.
- Switching chat to Gemini: you reported higher latency (not measured here); Groq remains the default, and the fallback model stays on Groq.
- More candidates, file-level score boosts, weighted RRF: all measured, all neutral or negative (see the Round 2 report).

## Open items from the plan

- **JevReranker**: base URL, model and key are stored and editable; the request/response mapping needs Jev's API details.
- **Phase 12 acceptance walkthrough** on a fresh clone has not been run end to end by a person; the automated suite and a live smoke test have (see the hand-off notes).
