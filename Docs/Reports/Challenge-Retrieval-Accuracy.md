# Challenge: making retrieval accurate, fast and honest

Written for: anyone asking "what challenges did you face building this?" (an interviewer, a reviewer, a new teammate). It tells the story of the hardest problem in DevDocs so far, with the numbers, the dead ends and what is still unsolved. Dates: 2026-10-10 to 2026-10-11. Technical detail lives in [Retrieval.md](../Plan/Retrieval.md) and [Retrieval-Ablation.md](Retrieval-Ablation.md).

## The 60-second answer

DevDocs answers questions about a user's codebase. Before the chat model is called, a retrieval step must (1) find the right pieces of code and docs and (2) **refuse** when the project does not contain the answer, so the model never makes something up. Getting both right was the hardest problem we met.

The first working version looked fine on a small sample but was wrong on a real project: it refused only 38% of off-topic questions. Fixing that exposed four deeper problems:

1. **We were grading ourselves too generously.** Thresholds were tuned and scored on the same questions. When we tested on 121 new questions, off-topic refusal fell from 92.7% to 86.0%.
2. **A component we trusted was hurting us.** A cross-encoder reranker, added to improve ranking, lowered accuracy and cost 2 to 4 seconds per question. The right file was already in the candidate pool 98.8% of the time; the reranker pushed it out of the top five.
3. **Every fix traded one metric for another.** Better refusal cost recall, better precision cost recall, speed cost accuracy. Tuning one number at a time kept going in circles.
4. **A headline metric hid a loss.** Hit rate stayed flat while a filter silently dropped 16% of the relevant chunks.

What we ended with: a pipeline that refuses by vector similarity (free, and a better signal than the reranker), orders by plain hybrid search, and trims weak chunks with a filter that protects exact keyword matches. On unseen questions it finds the right file **95.8%** of the time (old: 88.7%), answers in **1.1 s** (old: 3.5 s), and refuses **86%** of off-topic questions (old: 76%). It falls short on precision (37.7% of served chunks are from a relevant file; old 49.8%), and we say so.

## Where we started

| | |
|---|---|
| Pipeline | keyword search (BM25) + vector search → rank fusion (RRF) → reranker scores every candidate → drop chunks scoring below a threshold |
| Threshold | -10.7, chosen on an 11-file sample project with 29 questions |
| Promise | "no relevant context → don't call the LLM" |

## Challenge 1: a threshold that worked on the sample failed on a real project

**What happened.** On the 11-file sample every chunk was a candidate for every question, so off-topic scores were low and -10.7 looked fine. We then ingested DevDocs itself (104 files, 536 chunks), where an off-topic question has hundreds of chunks to match by accident. The same -10.7 refused only **38%** of off-topic questions.

**What we did.** Built a 307-question dataset on the real project (243 answerable, 55 off-topic, 9 borderline), validated every expected file path, and re-picked the threshold: **-5.5**, with a bootstrap range of -6.9 to -3.8.

**Lesson.** A threshold is a property of the corpus, not just of the model. A sample project cannot validate it.

## Challenge 2: measuring honestly

Three separate traps, all of which we fell into at least once:

- **Tuning and scoring on the same questions.** Every in-sample number was optimistic. We added 5-fold cross-validation (tune on four fifths, score on the fifth) and, at the end, a **frozen held-out set**: 121 new questions written in a different style (casual, typos, vague), with the settings written into the dataset file and a runner that refuses to start if the configuration differs.
  - Cross-validated refusal was 92.7%. On the held-out set it was **86.0%**.
- **The evaluator was the author.** The questions were written by the same person who built the pipeline, against the real files, so real users will do worse. We reduced it (new style, new topics) but could not remove it.
- **The corpus contains the test.** The project indexes its own tests, so "best beaches in Goa" is literally present in a test file and the pipeline correctly finds it. We reclassified that as borderline and excluded the dataset files from the index checks.

**Lesson.** Decide how you will be fooled before you look at results. Freeze the settings before running the final test, and report the numbers that got worse.

## Challenge 3: the reranker was hurting accuracy

**How we found it.** We stopped asking "does the reranker help?" and measured each stage separately (an ablation):

| Stage | Hit | Recall@5 |
|---|---|---|
| BM25 only | 0.815 | 0.726 |
| Vector only | 0.959 | 0.866 |
| BM25 + vector (RRF) | **0.967** | **0.875** |
| + reranker | 0.938 | 0.848 |
| + threshold | 0.877 | 0.787 |

The reranker lowered recall and cost 2.7 to 3.7 s per question. Question by question it made 12 answerable questions worse and only 5 better. Its job was real but narrow: it was the only source of an absolute relevance score, so it was the only thing that could refuse an off-topic question.

**What we tried to rescue it.**
- Weighted fusion (favouring vector search): no gain.
- Scoring long chunks in overlapping windows (the model reads only 512 tokens): hit 0.877 → 0.877 at twice the time. Reverted.
- Reranking only the top 10 candidates: slightly better and 40% faster, but not enough.
- Six rerankers, including code-aware ones (jina-v2, bge-base, bge-v2-m3): the three that could plausibly help took **5 to 16 seconds** for five passages on a CPU. Only MiniLM-sized models fit a 2 s budget, and they were no better.

**The discovery.** The refusal signal did not need a reranker. The best vector similarity already computed by the vector search separated answerable from off-topic questions better (AUC **0.991** vs 0.982) and was free.

## Challenge 4: every fix traded one metric for another

With a threshold on the reranker score, raising refusal cost hit rate and the reverse. Changing one setting at a time kept producing sidegrades. We changed the question from "which threshold?" to "which complete pipeline?":

- Separated three jobs the reranker had been doing at once: **gate** (answer or refuse), **order** (which chunks first), **filter** (drop weak chunks).
- Replayed **2,222 pipelines** (every combination of gate, order, filter and reranker model) from recorded candidates in about a minute, with cross-validation, against four stated goals: hit ≥ 0.93, refusal ≥ 0.891, relevant share ≥ 0.53, latency ≤ 2 s.
- Result: gate on vector similarity, order by plain RRF, reranker optional and off. On the tuning set (cross-validated) this met three of the four goals (hit, refusal, latency) and came close on the fourth (relevant share 0.512 vs 0.53); the first pipeline met none.

**Lesson.** When tuning one knob keeps trading metrics, the problem is the structure, not the value. Separate the jobs and replay offline so experiments cost seconds, not hours of CPU.

## Challenge 5: a headline metric hid a loss

The first recommendation for the filter (keep chunks within 0.025 of the best vector similarity) kept hit rate at 0.938. A reviewer questioned whether 0.025 was too strict, and a direct count of relevant chunks showed it was:

| Filter setting | Hit | Relevant chunks lost (of 552) |
|---|---|---|
| Gate only | 0.938 | 0 |
| Cut-off 0.025 + keep best 2 BM25 hits | 0.938 | **88 (16%)** |
| **Cut-off 0.04 + keep best 2 BM25 hits (shipped)** | **0.947** | **15 (3%)** |

Hit rate needs only one relevant chunk to survive, so it barely moved while the model would have lost 16% of the relevant material. Exact-match chunks (config names, identifiers) embed poorly, so a vector-only filter cuts them; protecting the two best keyword matches fixed most of it. Protecting three let weak chunks back in.

**Lesson.** Pick metrics that can see the failure you fear, and add a second one when a single number looks too good.

## Challenge 6: practical obstacles

- **API quota.** The free Gemini tier allows 1,000 embedding calls a day. Ingesting the project cost about 540 and the 307-question evaluation about 300. We cached every query embedding, made evaluations resumable on a quota error, and kept all analysis scripts offline. One gap remains: a daily 429 during ingestion marks the remaining files `failed` instead of pausing.
- **The index lags the code.** The indexed DevDocs snapshot predates our own changes, so held-out questions were written only about files that had not changed since ingestion, with expected files checked against the index.
- **Latency is not all ours.** Measured end to end, 1,062 ms of the new 1,122 ms median is the Gemini query-embedding call. Retrieval itself takes 60 ms. We had quoted "about 0.1 s" before measuring; the real figure is 10 times that, and correcting it in the docs was part of the work.
- **Environment friction.** Docker was not running, shell quoting broke long edit scripts, and the reranker bake-off had to download several large models. None was hard, all cost time.

## What we did not solve

| Gap | Evidence | Planned fix |
|---|---|---|
| Off-topic refusal misses its goal on new questions | 86.0% vs 89.1%; 7 of 50 leaked, all just above the gate (similarity 0.822 to 0.838) | re-pick the gate on more questions; the band between "leaks" and "wrongly refused" is under 0.02 wide |
| Precision | 37.7% of served chunks come from a relevant file (old pipeline 49.8%) | contextual chunk headers; revisit the cut-off with more data |
| Questions whose answer lives in config or rules files | config-value hit 0.57 to 0.64 in the tuning set; 1 of 3 held-out misses was the upload-rules file (the other two served tests and UI files ahead of the source file) | prepend file path and section to the embedded text (about 540 Gemini calls, needs a re-tune) |
| One project, one embedding model | the gate value 0.82 is tied to `gemini-embedding-001` at 1,536 dimensions | validate on the e-commerce sample project |
| Questions written by one person | every number is likely optimistic | collect real questions once chat exists (Phase 7) |
| A code-aware reranker | too slow on this CPU | revisit with a GPU or a hosted reranker through the `Reranker` interface |

## Tried afterwards, not shipped

After the held-out run we asked what could raise the share of relevant chunks. Replaying filters on the tuning-set records (gate 0.82; held-out set untouched):

- **Protect only strong BM25 matches** (score ≥ 14 instead of any top-2 hit): share 0.509 → 0.522 with no loss of hit or relevant chunks. This is the only free gain found; it is not applied yet because the absolute BM25 score depends on query length and corpus, and it should pass one held-out check first.
- **Everything else bought precision with relevant chunks:** an adaptive cut at the first big similarity drop, keeping only the best 2 to 3 files, capping chunks per file, and a combined vector + BM25 score reached a share of 0.51 to 0.68 but lost 3 to 42% of the relevant chunks and, for the adaptive and combined variants, dropped hit to 0.87 to 0.95.
- **Conclusion:** at the current signal quality the filter is close to its limit; further gains need better ranking signals (contextual chunk headers, a code-aware reranker on faster hardware) or used-chunk citations in Phase 7.

## What changed in the product

- Refusal gate: best vector similarity must reach **0.82**.
- Order: plain RRF; the reranker is **optional and off** (`RERANK_TOP_N=0`), the interface stays.
- Filter: keep chunks within **0.04** of the best similarity, plus the **2 best BM25 hits**.
- Everything is a setting; the Playground shows the gate decision and why each chunk was dropped; `python -m eval.run` re-tunes the gate; `python -m eval.holdout` repeats the frozen-set test.
- 111 backend tests pass; the frontend builds.

## Numbers over time

| Step | Hit | Off-topic refused | How measured |
|---|---|---|---|
| First pipeline (rerank all, -10.7) | 0.938 | 0.38 | tuning set |
| Rescored at -5.5 | 0.877 | 0.89 | tuning set, in-sample |
| Same pipeline, fair scoring | 0.881 | 0.836 | tuning set, cross-validated |
| New pipeline | 0.947 | 0.927 | tuning set, cross-validated |
| First pipeline on unseen questions | 0.887 | 0.760 | held-out, 121 questions |
| **New pipeline on unseen questions** | **0.958** | **0.860** | held-out, 121 questions |

## Lessons

1. **Measure every stage**, not just the end result; the component we assumed was helping was hurting.
2. **Separate jobs that one component does at once**, then replay offline so each idea costs seconds.
3. **Distrust in-sample numbers**; freeze settings and test on new questions, and publish the drop.
4. **Use more than one metric**; the one that looks best is the one most likely to hide a loss.
5. **Measure latency end to end**; the biggest cost was a part we had not measured.
