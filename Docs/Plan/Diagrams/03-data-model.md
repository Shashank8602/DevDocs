# 03 — Data Model

PostgreSQL schema and the Elasticsearch document. Source: Plan → Data Model.

## PostgreSQL

```mermaid
erDiagram
    PROJECTS ||--o{ FILES : "has (cascade)"
    PROJECTS ||--o{ CHUNKS : "has (cascade)"
    PROJECTS ||--o{ CONVERSATIONS : "has (cascade)"
    FILES ||--o{ CHUNKS : "split into (cascade)"
    CONVERSATIONS ||--o{ MESSAGES : "contains (cascade)"

    PROJECTS {
        uuid id PK
        text name
        text description
        text status "empty|processing|ready|failed|needs_reindex"
        text embedding_fingerprint
        timestamp created_at
        timestamp updated_at
        timestamp last_ingested_at
    }
    FILES {
        uuid id PK
        uuid project_id FK
        text file_path "unique per project"
        text file_name
        text file_type
        text language
        bigint file_size
        text file_hash "SHA-256"
        text status "pending|processing|ready|failed"
        text error_message "internal only"
        timestamp updated_at
    }
    CHUNKS {
        uuid id PK
        uuid project_id FK
        uuid file_id FK
        int ordinal
        text content
        int line_start
        int line_end
        int page_number
        text section
        int token_count
    }
    CONVERSATIONS {
        uuid id PK
        uuid project_id FK
        text title
        text summary
        uuid summary_checkpoint_message_id
        int recent_token_estimate
        timestamp created_at
        timestamp updated_at
    }
    MESSAGES {
        uuid id PK
        uuid conversation_id FK
        text role "user|assistant"
        text content
        jsonb sources_json "assistant only, frozen"
        text gen_state "pending|answered|failed|stopped|dismissed (user msgs)"
        timestamp created_at
    }
    SETTINGS {
        int id PK "single row"
        jsonb config "API keys Fernet-encrypted"
    }
```

## Elasticsearch document

Index name: `devdocs-chunks-{fingerprint}` (one per provider + model + dimensions).

```mermaid
classDiagram
    class ChunkDoc {
        _id : chunk_id
        project_id : keyword
        file_id : keyword
        file_path : keyword
        file_type : keyword
        content : text (code-aware analyzer)
        embedding : dense_vector(1536, cosine)
    }
```

## Postgres ↔ ES consistency

```mermaid
flowchart LR
    W["Write"] --> PGW["1. Write chunks to Postgres"]
    PGW --> ESW["2. Bulk index to ES<br/>refresh=wait_for"]
    ESW -- ok --> Ready["3. file = ready"]
    ESW -- error --> Failed["file = failed"]

    R["Read (retrieval)"] --> Q["ES query"]
    Q --> X["Intersect with Postgres<br/>ready file_ids"]
    X --> Out["Safe results"]

    D["Delete"] --> Cas["Postgres cascade"]
    D --> DBQ["ES delete_by_query<br/>file_id / project_id"]

    Rec["reconcile command"] --> Orph["Remove ES docs with<br/>no matching file"]
```
