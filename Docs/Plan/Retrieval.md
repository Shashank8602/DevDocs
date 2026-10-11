# Retrieval (Phase 6)

How a question becomes the final context, how the gate and the filter were chosen, and how to re-tune them. The flow diagram is [05 — Retrieval pipeline](Diagrams/05-retrieval-pipeline.md); file order is in [Backend-File-Flow.md](../Backend-File-Flow.md). The decision and its evidence are in [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md); earlier history (the first pipeline, which used a reranker threshold) is in [Reranker-Threshold-Eval.md](../Impl_Clarifications/Reranker-Threshold-Eval.md), [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md) and [Retrieval-Experiments.md](../Reports/Retrieval-Experiments.md). The story of how it got here is in [Challenge-Retrieval-Accuracy.md](../Reports/Challenge-Retrieval-Accuracy.md).

## Pipeline

| Step | Where | Notes |
|---|---|---|
| Eligible set | `pipeline.run` | `project_id` + Postgres READY file ids; never from the client. No READY file → no ES or embedding call. |
| BM25 + kNN | `bm25.py`, `vector.py` | Top-10 each, concurrently, isolation filter inside the query. ES errors retried twice (connection, 429, 502–504) then `RetrievalError`. |
| RRF (k=60) | `fusion.py` | In application code. Each candidate keeps its BM25 rank and its vector similarity. Candidates are joined to Postgres chunks of READY files, so a stray ES doc never gets through. |
| **Gate** | `selection.gate_passes` | Refuse the question (return `NoRelevantContext`, no LLM call) unless the **best vector similarity ≥ `0.82`**. Free: it is the top score of the kNN search. |
| Optional rerank | `reranker.py` | **Off by default** (`RERANK_TOP_N=0`). When on, the first N candidates are scored and served first in score order; it never refuses and never filters. |
| **Relative filter** | `selection.relative_filter` | Keep a chunk only if its vector similarity is within **`0.04`** of the best chunk's, **or** it is one of the **2 best BM25 hits**. |
| Select | `selection.select` | overlap cleanup → final Top-K 5 → ~3,000-token budget, in serving order. |
| Result | `pipeline.retrieve` | final chunks, or `NoRelevantContext(reason)`; the reason says why (`gate: top vector similarity 0.79 < 0.82`). |

Overlap cleanup drops the lower-ranked chunk when two chunks of the same file have overlapping line ranges; ranges are never merged (citations keep ranges separate). Chunks without line ranges (PDF pages, DOCX) are never considered overlapping.

### Why these three settings

- **Gate on vector similarity, not on a rerank score.** On the 298 scored DevDocs questions the best vector similarity separates answerable from off-topic questions better than the reranker does (AUC 0.991 vs 0.982) and costs nothing. With 5-fold cross-validation it refuses 92.7% of off-topic questions, against 83.6% for the first pipeline.
- **RRF order, not rerank order.** The MiniLM reranker orders code chunks worse than RRF (Recall@5 0.848 vs 0.875), and the right file was already in the candidate pool for 98.8% of questions.
- **Relative filter with a BM25 exception.** The filter lifts the share of served chunks that come from a relevant file from 0.47 to about 0.51, but on its own it cuts exact-match chunks (identifiers, config names) whose embeddings are weak. Protecting the 2 best BM25 hits recovers them. Measured at the gate of 0.82 (all 243 answerable questions): cut-off 0.04 + BM25 top 2 loses 15 of 552 relevant chunks (3%); cut-off 0.025 loses 88 (16%); BM25 top 3 lets weak chunks back in and loses more slots than it saves. Details: [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md).
- **The reranker is optional because it did not earn its latency.** At the same hit rate it adds about +0.01 to +0.02 of relevant share for about 0.6 s per question. The code-aware models that could beat RRF (jina-v2, bge) take 5 to 16 s for 5 passages on this CPU. Turn it on with `RERANK_TOP_N=5`; the `Reranker` interface and the `JevReranker` adapter stay for a hosted model later.

### Measured on the DevDocs project (104 files, 536 chunks; 243 answerable + 55 off-topic questions)

| Pipeline | Hit | Recall@5 | Off-topic refused | Relevant share of served chunks | End to end |
|---|---|---|---|---|---|
| First Phase 6 pipeline (rerank everything, chunk threshold -5.5), tuning set, cross-validated | 0.881 | 0.791 | 0.836 | 0.521 | ~3.7 s (est.) |
| **This pipeline**, tuning set, cross-validated | **0.947** | **0.853** | **0.927** | **0.512** | not timed |
| This pipeline, tuning set, `eval.run` on the real code (in-sample) | 0.959 | 0.865 | 0.927 | 0.509 | not timed |
| First pipeline, **held-out** (121 new questions, frozen settings) | 0.887 | 0.754 | 0.760 | 0.498 | 3.5 s p50 / 4.1 s p95 (measured) |
| **This pipeline, held-out** | **0.958** | **0.915** | **0.860** | 0.377 | **1.1 s p50 / 1.3 s p95 (measured)** |

Hit = a relevant file is among the served chunks. The questions were written by the pipeline's author against the real files, and 0.04 / top-2 were picked after seeing results, so expect smaller gains on real questions. The held-out rows are the honest ones: new questions, settings frozen beforehand, nothing tuned on them. About 95% of the new end-to-end time is the Gemini query-embedding call (p50 1.06 s). On held-out data the pipeline misses the refusal goal (0.860 vs 0.891) and the relevant-share goal (0.377 vs 0.53); see [Retrieval-Ablation.md](../Reports/Retrieval-Ablation.md).

### Known weak spots

- **Config-value questions** ("What is the embedding batch size?") hit 0.64: the answer sits in `config.py` / `.env.example`, which embed poorly. The planned fix is contextual chunk headers (file path and section prepended to the embedded text), which needs a re-embed and a re-tune of the gate.
- **The gate band is narrow.** Hit falls quickly above about 0.835 (on the held-out set, between 0.8275 and 0.84); all 7 held-out leaks sat at 0.822 to 0.838; off-topic questions close to the project's own vocabulary ("What is the license of this project?") still get through; 7 answerable questions sit just under 0.82, among them one-word identifier searches such as `webkitdirectory`.
- **The gate value is tied to the embedding fingerprint** (`gemini-embedding-001`, 1,536 dims). Any change of embedding config means re-running the eval and re-picking it. It has been validated on one project only.

## Re-tuning

```
cd backend
.venv/Scripts/python -m eval.run --dataset eval/dataset_devdocs.json       # gate sweep + cut-off table + per-tag + misses/leaks
.venv/Scripts/python -m eval.run --dataset eval/dataset_devdocs.json --cutoff 0.03 --protect 3
.venv/Scripts/python -m eval.run                                           # 29-question sample project (throwaway)
```

`eval.run` runs each question through the real pipeline once with the gate and filter off, then replays the gate (top vector similarity ≥ t), the relative filter and the selection offline across a grid of gate thresholds. It prints hit / Recall@5 / MRR / refusal per gate, the best plateau, a bootstrap range, a table of cut-offs at the configured gate (hit, Recall@5, relevant share, chunks served), per-tag results, borderline questions and every miss (marked GATE or FILTER/ORDER) and leak.

The eval needs docker-compose Postgres + ES and `GEMINI_API_KEY`. With the sample dataset it creates a throwaway `eval-<timestamp>` project and deletes it afterwards; with `existing_project` it only reads. Query embeddings are cached in `backend/eval/.cache/`, so a re-run re-embeds nothing and a full 307-question run takes under a minute with no reranker.

Deeper offline analyses (no API calls; need `eval/.cache/pipeline_records.json` from `python -m eval.experiments`): `eval.ablation` (per stage), `eval.experiments` (weighted RRF, refusal signal, rerank top N), `eval.rerankers` (reranker bake-off) and `eval.strategies` (2,222 complete pipelines with 5-fold cross-validation).

Dataset fields: `kind` (`answerable` | `offtopic` | `borderline`), `expected` (files that must be in the context), `acceptable` (also counts as a hit), `tag`, and file-level `exclude` / `path_prefix`.

For a single query, the Retrieval Playground (DEV_MODE) shows every candidate's vector similarity, the gate decision, why each chunk was dropped, and accepts overrides for the gate and the cut-off.

## Settings

| Env var | Default | Meaning |
|---|---|---|
| `GATE_VECTOR_THRESHOLD` | `0.82` | refuse the question below this top vector similarity |
| `RELATIVE_CUTOFF` | `0.04` | drop chunks further than this below the best chunk's similarity (`0` = no filter) |
| `BM25_PROTECT` | `2` | the best N BM25 hits are exempt from the filter |
| `RERANK_TOP_N` | `0` | optional reranker on the first N candidates (`0` = off) |
| `RERANKER_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | fastembed cross-encoder, used only when `RERANK_TOP_N > 0` |
| `RERANKER` | `cross_encoder` | `jev` selects the stub (raises until implemented) |
| `RETRIEVAL_FINAL_TOP_K` | `5` | max chunks in the context |
| `RETRIEVAL_CONTEXT_TOKENS` | `3000` | token budget (sized for Groq's 8,000 TPM) |
| `RETRIEVAL_LEG_K`, `RRF_K` | `10`, `60` | per-leg Top-K and RRF constant |
| `RETRIEVAL_MAX_RETRIES` | `2` | extra attempts on ES infrastructure errors |
