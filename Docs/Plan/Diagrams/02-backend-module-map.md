# 02 — Backend Module Map

Package layout under `backend/app/` and the allowed dependency direction. Source: Plan → Repository Layout.

```mermaid
flowchart TD
    main["main.py<br/>config.py, db.py, models.py, schemas.py"]

    subgraph api["api/"]
        a1["projects.py"]
        a2["files.py"]
        a3["conversations.py"]
        a4["settings.py"]
        a5["ingestion.py"]
        a6["dev.py<br/>(ES Inspector, Playground v1/v2)"]
    end

    subgraph ingestion["ingestion/"]
        i1["discovery, filters"]
        i2["change_detection, hashing"]
        i3["pipeline, worker"]
        i4["parsers/*"]
        i5["chunking"]
    end

    subgraph search["search/"]
        s1["es_client"]
        s2["index_manager"]
        s3["indexer, reconcile"]
    end

    subgraph retrieval["retrieval/"]
        r0["query<br/>keywords, follow-up"]
        r1["bm25, vector"]
        r2["fusion (RRF)<br/>+ test-file prior"]
        r3["reranker<br/>interface, cross_encoder, jev"]
        r5["selection<br/>threshold, overlap, budget"]
        r4["pipeline"]
    end

    subgraph generation["generation/"]
        g1["prompts"]
        g2["context_builder"]
        g3["chat<br/>(turn orchestration, SSE events)"]
        g4["memory (summary), citations,<br/>inspector (dev)"]
    end

    subgraph shared["shared services"]
        p["providers/<br/>llm, embeddings"]
        c["citations.py"]
        ss["settings_store<br/>(validation, Fernet keys)"]
        ri["ingestion/reindex"]
    end

    main --> api
    a5 --> ingestion
    a2 --> ingestion
    a1 --> ingestion
    a3 --> generation
    a3 --> c
    a4 --> ss

    i3 --> i4 --> i5
    i3 --> i2
    i3 --> p
    i3 --> search
    a6 --> r1
    a6 --> s3
    s3 --> s1

    r4 --> r1
    r4 --> r2
    r4 --> r3
    r4 --> r5
    a6 --> r4
    r1 --> search
    g3 --> r4
    g2 --> g1
    g3 --> g2
    g3 --> p
    g4 --> p
    ss --> p
    r4 --> r0
    r1 --> r0
    a4 --> ri
    ri --> p
    ri --> search
    g4 --> p
```

**Rules**
- `api/` is thin: validate, call a service, return.
- `retrieval/` and `generation/` never touch the Groq/OpenAI or Gemini SDKs; only `providers/` does.
- `change_detection.diff()` and `citations.py` are pure functions, so they are unit-testable without infrastructure.
