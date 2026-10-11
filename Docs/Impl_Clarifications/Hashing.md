# Hashing & Change Detection — Implementation Notes

Where: [`backend/app/ingestion/hashing.py`](../../backend/app/ingestion/hashing.py), [`change_detection.py`](../../backend/app/ingestion/change_detection.py), used by [`api/files.py`](../../backend/app/api/files.py). Added in Phase 3. Diagram: [12 — Change Detection](../Plan/Diagrams/12-change-detection.md).

## What is hashed
The **raw bytes of each uploaded file**, with **SHA-256** (`hashlib.sha256`). The hex digest (64 chars) is stored in `files.file_hash`. Path, name and timestamps are not part of the hash, so identical content at two paths gives the same hash, and a move changes the path but not the hash.

## Why 1 MB blocks
`copy_and_hash(src, dst)` reads the stream in `BLOCK_SIZE = 1024 * 1024` byte chunks. Each chunk is:
1. fed to the running SHA-256 (`digest.update(block)`), and
2. written to the destination file.

So the file is hashed **while it is being saved**, in one pass, and only 1 MB is ever in memory. A 20 MB file (the per-file limit) is never loaded whole. The result is identical to hashing the whole file at once; block size affects memory use only, never the digest.

It returns `(hex_digest, bytes_copied)`. The byte count becomes `file_size`, so the size stored is what was actually written, not what the client claimed.

## Upload flow (folder upload and re-upload)
1. Each accepted file is streamed into a temporary folder `projects/<id>/.staging-<random>/` and hashed on the way (`_stage`).
2. Stored `{path: hash}` is read from Postgres; uploaded `{path: hash}` comes from step 1.
3. `diff(old, new)` classifies every path (rules below).
4. Only then is anything changed:
   - **deleted**: file removed from disk, row removed.
   - **modified**: staged file moved over the old one, row gets the new hash and size, status → `pending`, error cleared.
   - **created**: staged file moved into place, new row with status `pending`.
   - **unchanged**: staged copy is discarded; the stored file, its row and its status (e.g. `ready`) are left alone.
5. The staging folder is always deleted (`finally`), so a failed upload leaves no half-written files in the project.

Staging first means a file that failed mid-upload can't overwrite a good stored copy.

## `diff()` rules
Pure function, no I/O: `diff(old: {path: hash|None}, new: {path: hash}) -> created, modified, deleted, unchanged` (each sorted).

| Condition | Result |
|---|---|
| in old, not in new | deleted |
| in new, not in old | created |
| in both, hashes differ | modified |
| in both, hashes equal | unchanged |
| rename / move | deleted (old path) + created (new path); no content-based rename detection |

Extra rule: a stored file whose `file_hash` is `NULL` (uploaded before Phase 3) counts as **modified**, since it can't be proven unchanged.

## Guard: empty uploads delete nothing
If every file in an upload is filtered out (unsupported, ignored, too large), the stored files are all reported **unchanged**, not deleted. Otherwise an accidental bad selection would wipe the project.

## Single-file replace (`PUT /files/{id}`)
Same staging + `copy_and_hash`. If the new hash equals the stored `file_hash`, nothing changes and the response is `changed: false` (no reprocessing). Otherwise the file is replaced at the **same path**, hash and size updated, status → `pending`. Over-size content returns 413.

## What the hash is *not*
- Not a security measure; it is only change detection.
- Not computed by the browser; the server always hashes what it received.
- Not used for deduplication across paths.
