# 10 — Settings Change & Re-index

What happens when settings change, especially embeddings. Source: Plan → Backend Behaviour §9; Phase 11.

## Which settings apply when

```mermaid
flowchart TD
    Save["PUT /settings"] --> Kind{"What changed?"}
    Kind -- "LLM, reranker, Advanced RAG" --> Next["Applies from the next query"]
    Kind -- "Embedding provider / model / dimensions" --> FP["New embedding fingerprint"]
    FP --> Mark["All projects → needs_reindex"]
    Mark --> Dis["Chat disabled,<br/>'Re-index' button shown"]
```

## Re-index sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend
    participant API as FastAPI
    participant W as Worker
    participant DB as PostgreSQL
    participant EMB as Embedding provider
    participant OLD as ES index (old fingerprint)
    participant NEW as ES index (new fingerprint)

    U->>FE: Click Re-index
    FE->>API: POST /projects/{id}/reindex
    API->>W: Enqueue re-index job
    W->>NEW: Create index if missing (new dims)
    loop batches of stored chunks
        W->>DB: Read chunks (no re-parse, no re-chunk)
        W->>EMB: Embed with new model
        EMB-->>W: vectors
        W->>NEW: Bulk index (same chunk IDs)
    end
    W->>OLD: delete_by_query project_id
    W->>DB: project.embedding_fingerprint = new<br/>status = ready
    FE-->>U: Chat enabled again
```

## Test connection

```mermaid
flowchart LR
    T["POST /settings/test"] --> Which{"Target"}
    Which --> L["LLM ping"]
    Which --> E["Embedding ping"]
    Which --> J["Jev ping"]
    L --> R{"OK?"}
    E --> R
    J --> R
    R -- yes --> Ok["Connected"]
    R -- no --> Bad["Generic failure message<br/>'Couldn't connect…'"]
```

**Notes**
- API keys are stored Fernet-encrypted, returned masked, and the visibility toggle works only for keys just typed.
- Unsaved changes are discarded when leaving the Settings page.
