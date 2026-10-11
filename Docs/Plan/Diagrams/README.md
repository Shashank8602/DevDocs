# DevDocs V1 — Diagrams

All diagrams are [Mermaid](https://mermaid.js.org/), derived from [DevDocs-V1-Implementation-Plan.md](../DevDocs-V1-Implementation-Plan.md) and [Phases.md](../Phases.md). View them in VS Code (Markdown Preview Mermaid Support extension), GitHub, or any Mermaid-aware viewer.

| # | Diagram | Type | Covers |
|---|---|---|---|
| 01 | [System architecture](01-system-architecture.md) | flowchart | Components and external services |
| 02 | [Backend module map](02-backend-module-map.md) | flowchart | Packages and dependency direction |
| 03 | [Data model](03-data-model.md) | ER / class / flowchart | Postgres schema, ES doc, consistency rules |
| 04 | [Upload & ingestion](04-upload-and-ingestion-sequence.md) | sequence | Folder upload → files `ready` |
| 05 | [Retrieval pipeline](05-retrieval-pipeline.md) | flowchart | Filter → BM25 + kNN → RRF → rerank → budget |
| 06 | [Chat streaming](06-chat-streaming-sequence.md) | sequence | SSE events, persistence, no-context path |
| 07 | [Stop / retry / cancel](07-stop-retry-cancel.md) | state + sequence | `gen_state` machine and failure handling |
| 08 | [Citations](08-citation-click-through.md) | flowchart + sequence | Citation builder, viewer, deleted-file case |
| 09 | [Summarization](09-conversation-summarization.md) | sequence + flowchart | Memory checkpoint and prompt assembly |
| 10 | [Settings & re-index](10-settings-and-reindex.md) | flowchart + sequence | Embedding fingerprint change and re-index |
| 11 | [Status lifecycle](11-file-status-lifecycle.md) | state | File and project statuses |
| 12 | [Change detection](12-change-detection.md) | flowchart | The 5 diff rules and worked example |
| 13 | [Frontend structure](13-frontend-structure.md) | flowchart + state | Pages, viewer renderers, chat states |
| 14 | [Phase roadmap](14-phase-roadmap.md) | flowchart + gantt | Phases 0–12, dev tools, spec coverage |

When the plan changes, update the matching diagram in the same edit.
