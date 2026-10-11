# 05 — Retrieval Pipeline

Hybrid retrieval from question to final context. Source: Plan → Backend Behaviour §5; Phase 6; decision in [Retrieval-Ablation.md](../../Reports/Retrieval-Ablation.md).

```mermaid
flowchart TD
    Q["User question"] --> Elig["Eligible set:<br/>project_id = current<br/>AND file status = ready<br/>(enforced by backend)"]
    Elig --> Par{{"Run concurrently"}}

    Par --> BM["ES BM25 match on content<br/>Top-K 10<br/>(code-aware analyzer)"]
    Par --> EQ["Embed question"] --> KNN["ES knn on dense_vector<br/>Top-K 10, num_candidates ~100"]

    BM --> RRF["RRF fusion (k = 60)<br/>in application code<br/>single ranking, no duplicates<br/>each chunk keeps BM25 rank + vector similarity"]
    KNN --> RRF

    RRF --> Gate{"Gate:<br/>best vector similarity<br/>>= 0.82 ?"}
    Gate -- no --> NRC["NoRelevantContext (reason: gate)<br/>LLM is NOT called"]
    Gate -- yes --> RR{"Optional reranker<br/>RERANK_TOP_N > 0 ? (default: off)"}
    RR -- "on" --> CE["Score first N candidates<br/>(CrossEncoder or Jev)<br/>serve them first, best score first"]
    RR -- "off" --> Flt
    CE --> Flt

    Flt["Relative filter:<br/>keep chunks within 0.04 of the best<br/>vector similarity<br/>OR among the 2 best BM25 hits"] --> Dedup["Overlap cleanup<br/>(same file, overlapping lines)"]
    Dedup --> Budget["Add in order until<br/>~3k token budget<br/>cap final Top-K 5"]
    Budget --> Ctx["Final context<br/>→ context builder"]

    BM -. "infra error" .-> Retry["Limited retries"]
    KNN -. "infra error" .-> Retry
    Retry -. "exhausted" .-> Err["RetrievalError"]
```

**Filter placement:** the `project_id` and ready `file_id` filter sits inside both the `match` and `knn` queries, so filtering happens before ranking. Results are also intersected with Postgres ready IDs.

**Why the gate is on vector similarity:** it is the best refusal signal measured (AUC 0.991) and is already computed by the kNN search; the reranker's score separated worse and cost 1 to 3 s. The reranker is therefore an optional ordering stage, off by default.
