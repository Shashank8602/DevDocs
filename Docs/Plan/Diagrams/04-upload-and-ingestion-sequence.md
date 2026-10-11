# 04 — Upload & Ingestion Sequence

From folder selection to files becoming `ready`. Source: Plan → Backend Behaviour §1; Phases 2–5.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend
    participant API as FastAPI
    participant DB as PostgreSQL
    participant FS as data/projects
    participant W as Ingestion worker
    participant EMB as Embedding provider
    participant ES as Elasticsearch

    U->>FE: Select folder (webkitdirectory)
    FE->>FE: Filter with rules from GET /upload-rules<br/>(ignore list, extensions, 20MB/50MB)
    FE->>API: POST /projects/{id}/upload (files + relative paths)
    alt project already ingesting
        API-->>FE: 409 Conflict
    else accepted
        API->>API: Re-apply same rules (upload_rules.json)
        API->>API: Hash files (SHA-256, 1MB blocks)
        API->>DB: Load stored {path: hash}
        API->>API: diff(old, new) → created / modified / deleted / unchanged
        API->>FS: Save created + modified files
        API->>DB: Delete rows for deleted files (cascade)
        API->>ES: delete_by_query for deleted files
        API->>DB: Upsert files as pending
        API->>W: Enqueue project job
        API-->>FE: Upload report (per-file accepted / rejected + reason; later: created, modified, deleted, unchanged)
    end

    loop each pending file
        W->>DB: status = processing
        W->>W: Parse → chunk (line / page / section metadata)
        W->>ES: Remove old chunks (if modified)
        W->>DB: Insert chunks
        W->>EMB: Embed chunks (batched, retry on 429)
        EMB-->>W: vectors
        W->>ES: Bulk index (refresh=wait_for)
        alt all steps succeed
            W->>DB: status = ready
        else any step fails
            W->>DB: status = failed (error_message internal)
        end
    end
    W->>DB: Derive project status, set last_ingested_at

    loop while ingestion active
        FE->>API: GET /ingestion/active
        API-->>FE: active projects
        FE->>FE: Update global indicator + badges
    end
```

**Notes**
- One failing file never fails the project.
- The worker keeps running if the user navigates to another project.
