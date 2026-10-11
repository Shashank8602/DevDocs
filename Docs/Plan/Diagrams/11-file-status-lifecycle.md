# 11 — File & Project Status Lifecycle

Source: Plan → Data Model, Backend Behaviour §1–2; Phase 4.

## File status

```mermaid
stateDiagram-v2
    [*] --> pending : created / modified on upload
    pending --> processing : worker picks up
    processing --> ready : parsed, chunked, embedded, indexed in ES
    processing --> failed : parse / embed / ES error
    failed --> pending : Retry
    failed --> pending : Replace File (new content)
    ready --> pending : modified (hash changed) on re-upload
    ready --> [*] : deleted (rows + ES docs removed)
    failed --> [*] : deleted
```

## Project status

```mermaid
stateDiagram-v2
    [*] --> empty : project created
    empty --> processing : files uploaded
    processing --> ready : at least one file ready, none pending/processing
    processing --> failed : every file failed
    ready --> processing : re-upload / replace
    failed --> processing : Retry / re-upload
    ready --> needs_reindex : embedding fingerprint changed
    needs_reindex --> processing : Re-index
    ready --> empty : all files deleted
```

**Notes**
- Project status is derived from file statuses; it is not set independently.
- Chat is enabled only when at least one file is `ready` and the project is not `needs_reindex`.
- Only `ready` files are retrievable.
