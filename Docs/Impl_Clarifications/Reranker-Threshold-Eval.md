# Reranker, Relevance Threshold & Eval Numbers — Implementation Notes

> **Historical note (2026-10-11).** This note describes the first Phase 6 pipeline, whose refusal gate was a threshold on the reranker score (-10.7, then -5.5). That pipeline was replaced by a vector-similarity gate with an optional reranker; see [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md) and [Retrieval.md](../Plan/Retrieval.md). The metric definitions and the history below still apply to the eval.

Where: [`backend/app/retrieval/reranker.py`](../../backend/app/retrieval/reranker.py), [`selection.py`](../../backend/app/retrieval/selection.py), [`pipeline.py`](../../backend/app/retrieval/pipeline.py), [`backend/eval/run.py`](../../backend/eval/run.py), datasets [`dataset.json`](../../backend/eval/dataset.json) and [`dataset_devdocs.json`](../../backend/eval/dataset_devdocs.json). Added in Phase 6. Design and pipeline overview: [Retrieval.md](../Plan/Retrieval.md). Diagram: [05 — Retrieval pipeline](../Plan/Diagrams/05-retrieval-pipeline.md).

Per-stage results (BM25 only, vector only, RRF, reranker, threshold) are in [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md): the reranker was found to lower recall slightly versus RRF alone.

This note is the reference for **what the numbers were, how they were measured, and what was tried**. Re-run the eval before trusting any of it on a different project or model.

## Current settings

| Setting | Value | Env var |
|---|---|---|
| Reranker | `Xenova/ms-marco-MiniLM-L-6-v2` (fastembed, local ONNX) | `RERANKER_MODEL` |
| Relevance threshold | **`-5.5`** (raw reranker score) | `RELEVANCE_THRESHOLD` |
| Final Top-K / context budget | 5 chunks / 3,000 tokens | `RETRIEVAL_FINAL_TOP_K`, `RETRIEVAL_CONTEXT_TOKENS` |
| Candidates per question | up to 20 (BM25 top-10 + vector top-10, merged by RRF k=60) | `RETRIEVAL_LEG_K`, `RRF_K` |

The threshold compares against the model's **raw logit**, not a probability. It is only meaningful for the model above; changing `RERANKER_MODEL` means re-running the eval.

## Current numbers (threshold -5.5, DevDocs project)

Measured on the ingested DevDocs project: 104 files, 536 chunks, real Gemini query embeddings, real reranker.

| Measure | Value |
|---|---|
| Answerable questions that got a relevant file (**hit**) | **0.877** (213 of 243) |
| **Recall@5** | **0.787** |
| **MRR** | **0.767** |
| Off-topic questions refused | **0.875** (49 of 56) as measured; **0.891** (49 of 55) on the current dataset |
| **J** (equal weight of hit and refusal) | 0.876 as measured; 0.884 on the current dataset |
| Answerable misses | 29 of 243 (12%) |
| Off-topic leaks | 7 of 56 as measured; 6 of 55 on the current dataset |

The "current dataset" has 55 off-topic questions because "best beaches in Goa" was moved to borderline: that exact string exists in `tests/test_retrieval.py`, so the pipeline legitimately found it. These numbers come from the run before the windowing experiment (below). It was reverted and the scores re-verified identical on spot checks, but the full eval was not re-run afterwards.

### Metric definitions

| Metric | Meaning |
|---|---|
| **hit** | At least one `expected` or `acceptable` file is in the final context |
| **Recall@5** | Share of the `expected` files present in the final context (top 5 chunks) |
| **MRR** | 1 / rank of the first expected or acceptable file in the final context |
| **refused** | Off-topic questions that returned `NoRelevantContext` (the LLM is not called) |
| **leak** | An off-topic question that still received context |
| **J** | `0.5 x hit rate + 0.5 x refusal rate` (weight is `--refuse-weight`) |

### Operating points (same run)

| Threshold | Hit | Recall@5 | MRR | Off-topic refused | J |
|---|---|---|---|---|---|
| none | 0.938 | 0.848 | 0.799 | 0.000 | 0.469 |
| -10.7 (previous default) | 0.938 | 0.844 | 0.799 | 0.375 | 0.657 |
| -8.5 | 0.930 | 0.836 | 0.797 | 0.732 | 0.831 |
| -6.5 | 0.922 | 0.822 | 0.792 | 0.804 | 0.863 |
| -6.05 | 0.905 | 0.801 | 0.788 | 0.839 | 0.872 |
| **-5.5 (current)** | **0.877** | **0.787** | **0.767** | **0.875** | **0.876** |
| -4.5 | 0.848 | 0.752 | 0.749 | 0.875 | 0.861 |
| -3.5 | 0.807 | 0.722 | 0.723 | 0.911 | 0.859 |
| -2.5 | 0.761 | 0.682 | 0.693 | 0.929 | 0.845 |
| 0.5 | 0.556 | 0.487 | 0.530 | 0.982 | 0.769 |

### How stable is -5.5

| Measure | Value |
|---|---|
| Best J on the sweep | 0.878 at -5.55 |
| Thresholds within 0.01 of best | -6.25 to -3.85 (width 2.40) |
| Bootstrap x500, median | -5.55 |
| Bootstrap 5-95% range | -6.9 to -3.8 |
| 70/30 split: tune picks / held-out J | -5.55 / 0.812 (best possible 0.853) |

### Hit rate by question type (at -5.55)

| Type | n | Hit | Recall@5 | MRR |
|---|---|---|---|---|
| identifier (e.g. `word_delimiter_graph`) | 25 | 0.96 | 0.94 | 0.92 |
| parse | 21 | 1.00 | 0.88 | 0.94 |
| provider | 15 | 1.00 | 1.00 | 0.91 |
| retrieval | 21 | 1.00 | 0.95 | 0.95 |
| search | 13 | 1.00 | 0.92 | 0.96 |
| worker | 11 | 1.00 | 1.00 | 0.91 |
| tests | 10 | 1.00 | 1.00 | 0.95 |
| arch | 17 | 0.94 | 0.76 | 0.75 |
| model | 10 | 0.90 | 0.65 | 0.73 |
| ingest | 26 | 0.85 | 0.65 | 0.65 |
| frontend | 29 | 0.72 | 0.71 | 0.52 |
| config | 14 | 0.71 | 0.50 | 0.51 |
| api | 16 | 0.69 | 0.59 | 0.62 |
| vague phrasing | 13 | 0.62 | 0.45 | 0.58 |

| Off-topic type | n | Refused |
|---|---|---|
| world knowledge | 20 | 0.90 |
| near-miss (plausible tech that is absent) | 24 | 0.92 |
| generic programming | 12 | 0.75 |

### Borderline questions (reported, never used for tuning)

| Question | Result at -5.5 | Why borderline |
|---|---|---|
| How does JWT token validation work? | context | only appears in test fixtures |
| How does chat streaming work? | context | not built yet; `check_providers.py` mentions streaming |
| best beaches in Goa | context | literal string exists in a test file |
| How does user authentication work? | refused | CLAUDE.md says there is no auth |
| Is there a caching layer? | refused | CLAUDE.md says V1 has no cache |
| How is the Redis cache configured? | refused | only the V1-has-no-cache sentence |
| How are conversations and messages stored? | refused | not built yet |
| Where is the payment retry mechanism? | refused | fixtures only; the Phase 7 proof |
| What guardrails does the system prompt enforce? | refused | system prompt not built yet |

## How the dataset was built

`backend/eval/dataset_devdocs.json`, 307 questions, every expected path validated against the project's real file list:

| Kind | Count | Notes |
|---|---|---|
| answerable | 243 | tagged by area (arch, config, ingest, parse, worker, provider, search, retrieval, api, model, frontend, tests, identifier, vague, eval) |
| offtopic | 55 | world, generic-code, near-miss |
| borderline | 9 | reported only |

- `expected` = files that must be in the context; `acceptable` = also counts as a hit (tests, CLAUDE.md, a related file).
- `exclude` removes `eval/dataset*.json` from the candidate pool, because the project contains them and they hold the question text.
- The questions were written by hand against the actual file contents, so they are cleaner than real user phrasing. Expect real-world numbers to be somewhat worse.

## History of changes

| Step | Threshold | What happened |
|---|---|---|
| Phase 6 first cut | -10.7 | Chosen from a 29-question, 11-file sample project. Recall@5 1.00, off-topic refused 0.86 there. |
| Eval on DevDocs itself | **-5.5** | The same -10.7 refused only 38% of off-topic questions on a real 104-file project (the best off-topic score is a maximum over many more chunks). Moved to -5.5. |
| Window scoring experiment | -5.5 | Scored overlapping 512-token windows of long chunks and took the best. Hit 0.877 -> 0.877, Recall@5 0.787 -> 0.796, MRR 0.767 -> 0.770, misses 29 -> 27, at about 2x rerank time. **Reverted.** |
| Lock files | n/a | `package-lock.json`, `pnpm-lock.yaml` added to the upload ignore list (60 of 536 chunks were lock files). Takes effect on re-upload. |

## Why the misses happen

- **Weak model for code, not truncation.** MiniLM-L-6 is a web-passage model. Truncation exists (44 real chunks exceed 512 tokens) but the window experiment showed it explains very few misses: several config misses are fields at the top of the chunk.
- **Generic programming questions leak** ("How do I reverse a linked list in Java?") because code-shaped text scores moderately against any code chunk.
- **The number depends on the corpus.** -10.7 on 11 files, -5.5 on 104 files. Re-tune on a project that looks like what users upload.

## Re-running

```
cd backend
.venv/Scripts/python -m eval.run --dataset eval/dataset_devdocs.json        # needs a project named "DevDocs" ingested
.venv/Scripts/python -m eval.run --dataset eval/dataset_devdocs.json -v     # per-question scores
.venv/Scripts/python -m eval.run                                            # 29-question sample project (throwaway)
```

- Needs docker-compose Postgres + ES. Query embeddings are cached in `backend/eval/.cache/`, so a re-run costs no Gemini calls for questions already embedded; new questions cost one embedding request each.
- A full 307-question run takes roughly 8-15 minutes (CPU reranking).
- Output: threshold table, suggested threshold (bootstrap median), tune/holdout result, per-tag table, borderline list, every miss and leak.

## Quota note

Gemini's free tier allows 1,000 embedding requests per day. A 104-file ingest costs about 540 requests and the 307-question eval about 300, so run them on different days or enable billing. A daily-quota 429 currently marks the remaining files `failed` instead of pausing ingestion; a quota-aware worker is a known gap.

## What would improve the numbers

1. A reranker that handles code better, or a second signal such as "the chunk appears in both the BM25 and vector lists".
2. Lowering the threshold (about -6.5) once the Phase 7 system-prompt guardrail exists: a leaked chunk then costs one LLM call, while a wrongly refused question is unrecoverable.
3. More and messier questions, ideally taken from real usage.
