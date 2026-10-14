# Retrieval and answer-phase optimisation, round 2

Written for: the project owner, who asked whether the retrieval mechanism (vector, BM25, RRF, reranker, filter, gate) could do better on the metrics, and how to make retrieval and the LLM answer phase faster on free-tier APIs. Date: 2026-10-14. Everything below was measured on this machine with the real pipeline; the scripts are in `backend/eval/`. Earlier rounds: [Challenge-Retrieval-Accuracy.md](Challenge-Retrieval-Accuracy.md), [Retrieval-Ablation.md](Retrieval-Ablation.md).

## Answer

Yes, two stages could be improved without touching the gate, and the answer phase had one large problem that was not about the model at all.

| Change | Shipped | Effect |
|---|---|---|
| BM25 searches the file path and heading as well as the body, with question words stripped | yes | share of served chunks from a relevant file +4.6 / +4.1 pts, MRR +2 pts, the right file is in BM25's top 10 for 92% / 96% of questions (was 87% / 83%) |
| Demote chunks of test files by 20% unless the question mentions tests | yes | MRR +3.5 / +6.9 pts, share +2.5 / +5.1 pts, hit +1.2 / +1.4 pts |
| Resolve follow-ups ("where is that stored?") by adding the previous question to the search text | yes | not measurable on the existing sets (they have no follow-ups); covered by tests |
| Settle token reservations to real usage; announce a wait instead of freezing | yes | the next question no longer waits for tokens the previous answer never used |
| Route to a second Groq model when the primary's per-minute budget is spent | yes | third question inside a minute: 43 s wait → 1.6–2.0 s |
| Keep-alive connection and LRU cache for query embeddings | yes | warm retrieval 1.1–1.3 s → 0.5–0.8 s; a repeated question 30–70 ms |
| Reuse vectors of unchanged chunks when a file is modified | yes | a modified file re-embeds only its new or changed chunks (test: an 8-function file plus one new function embeds fewer chunks than the file has); saves quota on every edit |

## Retrieval: what was measured

428 questions, cached query embeddings (no API calls), gate 0.82, cut-off 0.04, BM25 protect 2, reranker off. "Tuning" = 307 questions (243 answerable, 55 off-topic), "held-out" = 121 (71 answerable, 50 off-topic).

| Variant | Hit | Recall@5 | MRR | Share | Refused |
|---|---|---|---|---|---|
| Before (tuning / held-out) | 0.930 / 0.944 | 0.832 / 0.887 | 0.776 / 0.705 | 0.462 / 0.331 | 0.927 / 0.840 |
| + BM25 on path and section, stopwords stripped | 0.930 / 0.930 | 0.850 / 0.901 | 0.799 / 0.723 | 0.508 / 0.372 | 0.927 / 0.840 |
| **+ test-file prior ×0.8 (shipped)** | **0.942 / 0.944** | **0.867 / 0.915** | **0.834 / 0.792** | **0.533 / 0.423** | 0.927 / 0.840 |

Hit = any relevant file among the served chunks. Share = fraction of served chunks from a relevant file. The refusal rate does not move because the gate is untouched.

### Tried and not shipped

| Idea | Result | Why not |
|---|---|---|
| Boost files that have several candidate chunks (file-level score, α 0.25 to 1.0) | hit −5 to −9 pts (tuning), −18 to −24 pts (held-out) | one large file floods the Top-5 and pushes the right file out |
| 20 candidates per leg instead of 10 | hit −2 / −7 pts; BM25 pool +5 pts on tuning, none on held-out | more candidates, same Top-5: the extra ones displace good chunks |
| Weighted RRF (vector ×1.5 or BM25 ×1.5) | within ±1 pt | no gain over k=60 |
| Embed "path > section" with each chunk (contextual headers; scratch index, 536 chunks) | tuning: hit +0.4, MRR +2.2, refused +1.8 pts; held-out: hit +1.4, MRR +6.5, but Recall@5 −2.8 and refused −2.0 pts | mixed, and it costs a full re-embed of every project and a gate re-tune. The BM25 path/section fields capture part of the same signal for free |
| Rescue answerable questions just under the gate with BM25 evidence | best rule: +2 answerable, +1 off-topic leak, out of 5 / 28 in the band | too few cases to separate; the gate band stays the main limit |
| Demote migration files like tests | no change | not worth a rule |

### What did not change

The gate (0.82), the filter (0.04, protect 2), RRF k=60 and the reranker (off) stay as they were. The measurements again say the gate is the dominant factor: hit falls from 0.93 to 0.90 between 0.82 and 0.83 on the tuning set, and refusal rises from 0.93 to 0.96. Off-topic refusal is still 0.84 on the held-out questions, below the 0.891 goal.

## Answer phase: what was measured

Live against Groq `openai/gpt-oss-120b` and the DevDocs project, same machine, one run each (single runs; Groq latency varies by about ±1 s).

| | Before | After |
|---|---|---|
| Query embedding (the retrieval cost) | 1.1–1.3 s | 0.5–0.8 s warm, 30–70 ms for a repeated question |
| First token, gpt-oss-120b, effort low | 1.5–3.5 s | unchanged (the model's reasoning time) |
| First token, gpt-oss-20b, effort low | 1.0 s | used as the fallback |
| Question 3 within one minute | 43 s wait, silent | 1.6–2.0 s total on the fallback model; the UI shows "waiting" if both are spent |
| 6 questions inside one minute | n/a | the later ones wait (both models' 7,000-token budgets are spent); the UI says so |

### Why back-to-back questions stalled

Groq's free key allows 8,000 tokens per minute per model. One question costs about 2,500 to 3,500 tokens (system prompt, up to 3,000 tokens of context, history, and the answer including hidden reasoning). Two questions fill the window. The old code reserved prompt + 1,500 output tokens per call and never gave the unused part back, so the wait was longer than necessary and nothing told the user. Now:

1. the reservation is corrected to the real usage when the stream ends (`stream_options.include_usage`);
2. the chat announces the wait (`waiting` event) instead of looking frozen;
3. Groq's limits are per model, so a second model (`openai/gpt-oss-20b`) has its own budget. The router uses it only when the primary would wait more than 6 s and the fallback would wait less. Both are Groq; nothing switches to Gemini.

Not measured: answer quality of 20b against 120b. It is a smaller model, so the fallback is a trade of some quality for a long wait. It can be turned off (Settings → LLM → Fallback model, empty) or the primary can be set to 20b.

Models available on this key (checked 2026-10-14): `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`. `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` return 404 on it.

## Caveats

- The "frozen" held-out set was used to choose between the BM25 variants and the test-file prior, so it is no longer an untouched test. Both ideas were picked from a handful of candidates and improved both sets in the same direction, but expect smaller gains on real questions.
- All questions were written by one person for one project (DevDocs itself, whose tests are part of the corpus). The test-file prior is the change most specific to that corpus; on a project where users ask about tests it is skipped only if the question contains the word "test".
- The DevDocs index is a snapshot from before these changes, so questions about newer code are not in it.
- Latency numbers are single live runs.

## Reproduce

```
cd backend
.venv/Scripts/python -m app.search.backfill             # once: fill path_text / section on an existing index
.venv/Scripts/python -m eval.bm25_variants              # BM25 variants and the test-file prior, both datasets
.venv/Scripts/python -m eval.context_headers embed      # (quota: ~1 call per chunk) then: eval.context_headers eval
.venv/Scripts/python -m eval.gate_band                  # BM25 evidence inside the gate band
.venv/Scripts/python -m eval.llm_latency PROMPT.json openai/gpt-oss-120b:low openai/gpt-oss-20b:low
```

`PROMPT.json` is the output of `GET /conversations/{id}/context` (DEV_MODE).
