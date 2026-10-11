# ES Inspector (dev only, Phase 5)

Shows whether Elasticsearch agrees with Postgres for one project. Its main use is proving that deleting a file or project also removes its ES docs. Hidden unless `DEV_MODE=true` (same gating as the [Chunk Inspector](Chunking.md)).

## How you open it

In the project's **Files** tab, a dev-only **ES Inspector** button sits in the tab's toolbar (next to the upload actions). Clicking it opens a modal panel, the same way **Chunks** opens the Chunk Inspector. The panel has a **Refresh** button; it does not auto-update, so after deleting a file you click Refresh to see the new counts.

## How the panel looks

```
┌ ES Inspector — banking-demo ─────────────────────────── [Refresh] [✕] ┐
│ ES index: devdocs-chunks-gemini-gemini-embedding-001-1536              │
│ Total chunks   Postgres: 10    Elasticsearch: 10    ✔ in sync          │
│                                                                        │
│ File            Status       Postgres chunks   ES chunks   Match       │
│ auth.py         Ready              6               6         ✔         │
│ jwt.py          Ready              4               4         ✔         │
│ notes.md        Processing         3               0         – (not indexed yet)
│ broken.pdf      Failed             0               0         –         │
│                                                                        │
│ Orphans in ES (docs whose file no longer exists in Postgres): none     │
└────────────────────────────────────────────────────────────────────────┘
```

Rules for the rows:
- **Ready** files must match exactly; a mismatch is highlighted red with ✖.
- **Processing / Failed** files are expected to have 0 ES chunks and are shown with "–" (never a mismatch).
- **Orphans** lists ES docs grouped by `file_id` for files Postgres no longer has, with their counts. If any appear, the panel suggests running `reconcile`. After a normal delete this list stays empty.

## Vector peek

Each file row with ES chunks has a **Peek** button that expands the stored embeddings for that file, one card per chunk:

- chunk ordinal, line range or page, token count;
- `dims`, L2 `norm`, `min` and `max` of the vector (Gemini's 1536-dim truncated vectors are not unit length, which is why the index uses cosine);
- a small bar chart (blue positive, red negative) and the first 16 values;
- **Show all values** loads every value (the chart then averages all 1536 into 96 bars, and the full list is shown in a scrollable box).

Backend: `GET /projects/{project_id}/files/{file_id}/vectors[?full=true]`, dev-only, filtered by both `project_id` and `file_id`. Chunk metadata comes from Postgres; vectors come from ES.

## What the delete proof looks like

1. Open the panel: `auth.py` shows `6 / 6 ✔`, total `10 / 10`.
2. Close it, delete `auth.py` in the Files tab, open (or Refresh) the panel.
3. The `auth.py` row is gone, the total reads `4 / 4`, and Orphans is `none`.
4. If ES delete had failed, the row would be gone from Postgres but `auth.py` would appear under **Orphans** with 6 docs, so the failure is visible.
5. Delete the whole project: the panel is unreachable (the project is gone), so the check is `GET /projects/{project_id}/es-count` (or the `curl` ES `_count` against the project's id), which returns 0 even though the project no longer exists in Postgres.

## Backend

- `GET /projects/{project_id}/es-inspector` returns 404 unless `DEV_MODE=true`.
- Postgres side: chunk counts per file, joined with file status.
- ES side: a `terms` aggregation on `file_id` (size = number of project files + a margin) over the current fingerprint index, filtered by `project_id`; a second aggregation for `file_id`s not in the Postgres file list gives the orphans.
- Response: `{index, totals: {postgres, es}, files: [{file_id, path, status, postgres_chunks, es_chunks}], orphans: [{file_id, es_chunks}]}`.
- `GET /projects/{project_id}/es-count` (dev only) returns just the project's ES doc count, used for the post-project-delete check.
