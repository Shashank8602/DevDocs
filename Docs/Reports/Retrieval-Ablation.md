# Retrieval Pipeline Ablation Report

Updated 2026-10-11 for the **current pipeline** (vector-similarity gate 0.82, relative filter 0.04, BM25 top 2 protected, reranker optional and off). The first version of this report (2026-10-10) measured the earlier pipeline (reranker on every candidate, chunk threshold -5.5); that pipeline is kept below as the baseline, called "first pipeline". Script: [`backend/eval/ablation.py`](../../backend/eval/ablation.py). Datasets: [`dataset_devdocs.json`](../../backend/eval/dataset_devdocs.json) (tuning set, 243 answerable + 55 off-topic) and [`dataset_devdocs_holdout.json`](../../backend/eval/dataset_devdocs_holdout.json) (held-out set, 71 answerable + 50 off-topic, settings frozen before it was run). Background: [Retrieval.md](../Plan/Retrieval.md), [Challenge-Retrieval-Accuracy.md](Challenge-Retrieval-Accuracy.md).

## Summary

| Finding | Tuning set (in-sample) | Held-out set (new questions) |
|---|---|---|
| **The shipped pipeline finds the right file more often than the first pipeline.** Hit | 0.877 → **0.959** | 0.887 → **0.958** |
| **...and ranks more of the expected files in the top five.** Recall@5 | 0.787 → **0.865** | 0.754 → **0.915** |
| **It refuses more off-topic questions.** | 0.891 → **0.927** | 0.760 → **0.860** |
| **It serves a smaller share of relevant chunks.** Relevant share | 0.531 → 0.509 | 0.498 → **0.377** |
| **The vector gate alone does the refusing**, at no cost to hit on new questions. | hit −0.016 vs plain RRF | hit ±0.000 |
| **The relative filter lifts the share by about 4 points** and costs about one question. | share +0.043 | share +0.044, hit −0.014 |
| **Vector search is still the strongest single stage;** fusion adds little over it. | Recall@5 0.866 vs RRF 0.875 | 0.930 vs 0.901 |
| **The right file is nearly always already in the candidate pool.** The loss is in ordering and cutting. | 98.8% | 100% |

Read the two columns differently. The tuning set is **in-sample for both pipelines**: the first pipeline's -5.5 and the shipped 0.82 were both picked on it, so both are flattered. The held-out column is out-of-sample for both and is the one to trust. Cross-validated figures for the tuning set (5-fold) come from `python -m eval.strategies`; see [Retrieval.md](../Plan/Retrieval.md).

## 1. What was measured

Every question's candidate pool (up to 10 BM25 + 10 vector chunks, merged; mean 16.3 chunks on the tuning set, 17.6 on the held-out set) was re-ordered and re-cut the way each stage of the pipeline would have. **Every stage goes through the same post-processing as production**: overlap cleanup, Top-K 5 and the 3,000-token budget. So the stages differ only in how they rank and cut.

| Stage | What it does | Can refuse? |
|---|---|---|
| BM25 only | keyword results in BM25 order | no |
| vector only | kNN results in vector order | no |
| RRF | both lists fused (k = 60) | no |
| RRF + reranker (no cut) | MiniLM scores every candidate, best first | no |
| **first pipeline** | the above + chunk threshold -5.5 on the rerank score | yes |
| RRF + vector gate | RRF order; question refused if the best vector similarity < 0.82 | yes |
| **shipped** | gate + drop chunks > 0.04 below the best similarity, keeping the 2 best BM25 hits | yes |

Metrics: hit, Recall@5 and MRR over answerable questions; off-topic refusal; **relevant share** = the share of served chunks whose file is an expected or acceptable file (over answered questions). Definitions: [Reranker-Threshold-Eval.md](../Impl_Clarifications/Reranker-Threshold-Eval.md).

## 2. Results per stage

### Tuning set: 243 answerable + 55 off-topic

| Stage | Hit | Recall@5 | MRR | Off-topic refused | Relevant share | Chunks served |
|---|---|---|---|---|---|---|
| BM25 only | 0.815 | 0.726 | 0.623 | not possible | 0.360 | 4.84 |
| vector only | 0.959 | 0.866 | 0.804 | not possible | 0.478 | 4.99 |
| BM25 + vector (RRF) | **0.967** | **0.875** | 0.801 | not possible | 0.464 | 5.00 |
| RRF + reranker (no cut) | 0.938 | 0.848 | 0.799 | not possible | 0.451 | 4.99 |
| first pipeline (threshold -5.5) | 0.877 | 0.787 | 0.767 | 0.891 | **0.531** | 4.08 |
| RRF + vector gate 0.82 | 0.951 | 0.859 | 0.790 | **0.927** | 0.466 | 5.00 |
| **shipped** (gate + cut-off 0.04 + BM25 top 2) | **0.959** | **0.865** | 0.798 | **0.927** | 0.509 | 4.63 |

### Held-out set: 71 answerable + 50 off-topic

| Stage | Hit | Recall@5 | MRR | Off-topic refused | Relevant share | Chunks served |
|---|---|---|---|---|---|---|
| BM25 only | 0.690 | 0.521 | 0.475 | not possible | 0.187 | 4.99 |
| vector only | 0.944 | 0.930 | 0.769 | not possible | 0.366 | 5.00 |
| BM25 + vector (RRF) | **0.972** | 0.901 | 0.770 | not possible | 0.332 | 5.00 |
| RRF + reranker (no cut) | **0.972** | 0.852 | 0.788 | not possible | 0.358 | 4.99 |
| first pipeline (threshold -5.5) | 0.887 | 0.754 | 0.736 | 0.760 | **0.498** | 3.91 |
| RRF + vector gate 0.82 | **0.972** | 0.901 | 0.770 | **0.860** | 0.332 | 5.00 |
| **shipped** | 0.958 | **0.915** | 0.769 | **0.860** | 0.377 | 4.72 |

"Not possible" means the stage has no relevance score to cut on, so it returns five chunks for every question, including off-topic ones.

### What each step adds

| Step | Tuning set: hit / Recall@5 / share | Held-out: hit / Recall@5 / share |
|---|---|---|
| RRF over the better single leg (vector) | +0.008 / +0.009 / −0.013 | +0.028 / −0.028 / −0.034 |
| BM25 over vector (BM25 alone is worse) | −0.144 / −0.141 / −0.118 | −0.254 / −0.408 / −0.180 |
| Reranker over RRF (no cut) | **−0.029 / −0.027** / −0.013 | +0.000 / −0.049 / +0.026 |
| First pipeline's threshold over no cut | −0.062 / −0.060 / **+0.079** | −0.085 / −0.099 / **+0.139** |
| **Vector gate over plain RRF** | −0.016 / −0.016 / +0.002 | **+0.000 / +0.000 / +0.000** |
| **Relative filter + BM25 protect over the gate** | +0.008 / +0.006 / **+0.043** | −0.014 / +0.014 / **+0.044** |
| **Shipped over the first pipeline** | **+0.082 / +0.078** / −0.021 | **+0.070 / +0.162** / −0.121 |

## 3. By question type

### Recall@5, tuning set

| Type | n | BM25 | vector | RRF | first pipeline | shipped | shipped − first |
|---|---|---|---|---|---|---|---|
| api | 16 | 0.52 | **0.96** | 0.90 | 0.59 | 0.86 | **+0.27** |
| frontend | 29 | 0.53 | **0.95** | 0.91 | 0.71 | 0.95 | **+0.24** |
| vague | 13 | 0.27 | 0.77 | 0.56 | 0.45 | 0.64 | **+0.19** |
| model | 10 | 0.55 | 0.87 | 0.70 | 0.65 | 0.80 | +0.15 |
| ingest | 26 | 0.48 | 0.87 | 0.83 | 0.65 | 0.75 | +0.10 |
| parse | 21 | 0.95 | 0.93 | 0.98 | 0.88 | 0.98 | +0.10 |
| arch | 17 | 0.79 | 0.71 | 0.88 | 0.76 | 0.82 | +0.06 |
| identifier | 25 | **1.00** | 0.94 | 1.00 | 0.94 | 1.00 | +0.06 |
| search, tests, worker, eval | 36 | | | | | | 0.00 |
| retrieval | 21 | 0.95 | 0.81 | 0.90 | 0.95 | 0.90 | −0.05 |
| provider | 15 | 0.93 | 0.93 | 0.93 | 1.00 | 0.93 | −0.07 |
| **config** | 14 | 0.57 | 0.39 | 0.54 | 0.50 | **0.39** | **−0.11** |

### Recall@5, held-out set (small groups: one question moves a group by 10 to 33 points)

| Type | n | RRF | first pipeline | shipped | shipped − first |
|---|---|---|---|---|---|
| rules | 9 | 0.78 | 0.28 | 0.78 | **+0.50** |
| frontend | 11 | 1.00 | 0.64 | 1.00 | **+0.36** |
| search | 5 | 1.00 | 0.80 | 1.00 | +0.20 |
| infra | 7 | 1.00 | 0.86 | 1.00 | +0.14 |
| api, worker | 16 | 0.75 to 1.00 | 0.75 to 0.88 | 0.88 to 1.00 | +0.12 |
| model, provider, change | 13 | 0.60 to 1.00 | 0.60 to 1.00 | 0.60 to 1.00 | 0.00 |
| parse | 10 | 0.90 | 1.00 | 0.90 | −0.10 |

### Off-topic questions refused, by type (first pipeline → shipped)

| Type | Tuning set | Held-out set |
|---|---|---|
| generic programming | 0.75 → **1.00** (n=12) | 0.56 → **0.94** (n=16) |
| near-miss technologies | **0.92** → 0.83 (n=24) | **0.72** → 0.67 (n=18) |
| world knowledge | 0.95 → 1.00 (n=19) | 1.00 → 1.00 (n=16) |

## 4. Patterns

- **The vector gate fixes the refusal the reranker handled badly.** The first pipeline let many generic programming questions through (refused 0.56 on the held-out set); the vector gate refuses 0.94 to 1.00 of them.
- **But the gate is slightly worse on near-miss technologies,** the questions that use the project's own vocabulary about a technology it does not have ("where is the eslint config"). The reranker refused more of these on both sets (0.92 vs 0.83; 0.72 vs 0.67). That is where the 7 held-out leaks come from.
- **The filter trades a little hit for share.** It lifts the share by about 4 points on both sets. On the held-out set it also cost one question (hit 0.972 → 0.958): the gate itself cost nothing there.
- **BM25 is strong on identifiers and weak on natural language.** It scores 1.00 on identifier and 0.95 on parse questions on the tuning set and is the weakest stage on the held-out set (hit 0.690), where questions are casual ("whats the biggest single file size allowed"). Fusion keeps its benefit (identifiers) without paying for its weakness, since the vector leg dominates.
- **The reranker's ordering was not better than RRF's.** On the tuning set it lowered recall (−0.027); on the held-out set it was neutral on hit and lowered recall (−0.049). Question by question it was better and worse about equally often (held-out hit: 2 better, 2 worse).
- **Config-value questions got worse** (Recall@5 0.50 → 0.39). The answer sits in `config.py` or `.env.example`, whose chunks embed poorly, and the shipped pipeline now matches vector-only search on them (0.39). This is the main regression and the target of the contextual-headers follow-up.

## 5. Shipped vs first pipeline, question by question

| Measure | Tuning set: better / worse / same | Held-out: better / worse / same |
|---|---|---|
| Hit | **24** / 4 / 215 | **8** / 3 / 60 |
| Recall@5 | **35** / 13 / 195 | **14** / 2 / 55 |
| MRR | 49 / 40 / 154 | 21 / 17 / 33 |

Hit and Recall@5 improve for several times more questions than they worsen. MRR moves for many questions in both directions and nets to a small gain (+0.031 and +0.033).

## 6. Candidate pool ceiling

| Measure | Tuning set | Held-out |
|---|---|---|
| Mean candidates per question | 16.3 | 17.6 |
| Answerable questions with a relevant file in the pool | **98.8%** (240 of 243) | **100%** |

No ordering or cut of this pool can beat that ceiling. The shipped pipeline reaches 0.959 and 0.958, so at most about 4 points of hit remain to gain from better ordering and cutting. Raising the per-leg Top-K would not help.

## 7. Latency

| Measure | Value |
|---|---|
| Reranker, first pipeline, per question | mean 2,714 ms (158 ms per passage) on the tuning run; mean 3,332 ms, p50 3,161 ms (180 ms per passage) on the held-out run |
| Search + Postgres + RRF + gate + filter (shipped) | p50 60 ms, p95 66 ms (measured in `eval.holdout`) |
| Query embedding, live Gemini call | p50 1,062 ms, p95 1,256 ms (same run) |
| **End to end, shipped** | **p50 1,122 ms, p95 1,310 ms** |
| End to end, first pipeline | p50 3,523 ms, p95 4,063 ms |

Reranker time varies between runs on this CPU (2.4 s p50 in the latency run, 3.2 s in this one); the shipped pipeline does not call the reranker. The end-to-end figures are measured by `python -m eval.holdout`.

## 8. Interpretation

1. **Most of the improvement comes from changing what refuses, not from better search.** Search (BM25 + vector + RRF) was already at hit 0.967 in the first version. The first pipeline lost 9 points of hit to its reranker ordering and threshold; the shipped pipeline keeps that hit rate and refuses with a free signal.
2. **The remaining weakness is precision, not recall.** The shipped pipeline serves 4.6 to 4.7 chunks per answer and about 51% (tuning) or 38% (held-out) come from a relevant file. The ceiling for hit is 98.8% to 100%.
3. **Near-miss technologies are the unsolved refusal case.** The gate cannot tell "an eslint config" from "a config file" because the project's own vocabulary is close; a reranker (it refused more of these) or an LLM check may do better.
4. **Config-value questions need index-side help,** not a different cut.

## 9. Limits of this report

- **One project, one embedding model.** The conclusions are about `gemini-embedding-001` at 1,536 dimensions on a Python/TypeScript repository.
- **The tuning-set column is in-sample.** Both the -5.5 threshold of the first pipeline and the 0.82 gate / 0.04 cut-off of the shipped one were chosen on those questions. The held-out column is out-of-sample for both.
- **The datasets were written by the pipeline's author.** Real questions will be noisier and expect smaller gains. The held-out set is smaller (71 and 50), so differences under about 4 points are weak evidence, and each off-topic question is 2 points of refusal.
- **File-level scoring.** A hit means a relevant file reached the final five chunks, not that the exact passage did; the relevant share counts a chunk from a useful neighbouring file as irrelevant.
- **Latency was measured on an otherwise idle desktop CPU** with the reranker model already loaded.
- **The indexed project snapshot predates the current code.** The held-out questions are only about files unchanged since ingestion.

## 10. Reproduce

```
cd backend
# tuning set, from the saved records (seconds; records come from `python -m eval.experiments`)
.venv/Scripts/python -m eval.ablation --reuse eval/.cache/pipeline_records.json

# held-out set, live run through the real pipeline (about 6 min of CPU, mostly the reranker)
.venv/Scripts/python -m eval.ablation --dataset eval/dataset_devdocs_holdout.json --save eval/.cache/holdout_records.json
# ...and afterwards, in seconds
.venv/Scripts/python -m eval.ablation --dataset eval/dataset_devdocs_holdout.json --reuse eval/.cache/holdout_records.json
```

Both need the DevDocs project ingested (named "DevDocs") and the query-embedding cache from a previous `eval.run`; the script exits instead of calling the API if a question is not cached. The gate, cut-off and BM25 protect values come from the settings (`GATE_VECTOR_THRESHOLD`, `RELATIVE_CUTOFF`, `BM25_PROTECT`).
