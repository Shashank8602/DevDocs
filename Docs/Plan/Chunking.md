# Parsing & Chunking (Phase 4)

Every file goes parse → chunk → store. Parsers (`backend/app/ingestion/parsers/`) all return one `ParsedDocument`: a list of `Segment(text, line_start, line_end, page, section)`. The chunker (`ingestion/chunking.py`) works only on that shape, so it never cares about the file format.

## Segments by file type

| Type | One segment per | Metadata |
|---|---|---|
| Python | top-level function / class (decorators and the comment block above included); module-level code between them. A class over 60 lines is split into its header plus one segment per method (`Class.method`). | lines, section = name |
| Other code (JS, TS, Java, Go, Rust, C/C++, C#, Ruby, PHP, HTML, …) | the whole file; the chunker splits it with a language-aware splitter | lines |
| Markdown | heading (section = heading path, `Setup > Install`; headings inside code fences are ignored) | lines, section |
| PDF | page; pages without text are skipped, and a PDF with no text at all fails | page |
| DOCX | heading (tables flattened to `cell \| cell`) | section |
| JSON / YAML | top-level key; small keys are packed together up to the size cap | lines, section = `first – last` key |
| CSV | group of rows (about 3000 characters) with the header repeated; quoted multi-line records stay whole | lines of the rows |
| TXT, TOML, XML | the whole file, split by paragraph | lines |

## Size cap

A segment larger than **800 tokens** (estimated as characters / 4) is split further with **100 tokens** of overlap. Pieces keep the segment's `page` and `section`; they get exact line ranges when the segment has lines. This applies to every type, so a 5-page-long "page" of a PDF becomes several chunks that all say `page 5`.

## Rules

- Line numbers are 1-based and inclusive, counted after normalising CRLF to LF, so they match the file viewer.
- A file that cannot be parsed (corrupt PDF, binary content, no extractable text) fails alone; the project still becomes `ready` if any other file is `ready`.
- Chunks live in Postgres (`chunks` table). Replacing or modifying a file swaps its chunks in one transaction.

## Background worker (`ingestion/worker.py`)

One in-process asyncio consumer, one job per project. A job takes the project's `pending` files one by one: `pending → processing → ready | failed`, and the project status is re-derived after each file (`ingestion/status.py`). At startup, files left `processing` by a crash go back to `pending` and are re-queued.

- `POST /files/{id}/retry` queues a failed file again; `PUT /files/{id}` (replace) uses the same path.
- Upload and replace return 409 while the project is `processing`.
- `GET /ingestion/active` lists projects being ingested and feeds the global header indicator.

## Chunk Inspector (dev only)

`GET /projects/{project_id}/files/{file_id}/chunks` returns 404 unless `DEV_MODE=true`. With dev mode on, each file row in the Files tab has a **Chunks** action that opens the inspector (ordinal, line range, page, section, token count, text). `GET /config` tells the frontend whether dev mode is on.
