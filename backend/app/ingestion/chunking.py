"""Turns a ParsedDocument into chunks.

Each parser segment (function, heading section, page, group of rows) becomes one chunk. A segment
larger than MAX_CHUNK_TOKENS is split further with overlap; the pieces keep the segment's page and
section, and get exact line ranges when the segment has lines."""
from dataclasses import dataclass

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.ingestion.parsers import ParsedDocument, Segment

MAX_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 100
CHARS_PER_TOKEN = 4  # rough estimate; real tokenisation is the embedding model's business
MAX_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN

_LANGUAGES = {
    "python": Language.PYTHON, "javascript": Language.JS, "typescript": Language.TS, "java": Language.JAVA,
    "go": Language.GO, "rust": Language.RUST, "c": Language.C, "cpp": Language.CPP, "csharp": Language.CSHARP,
    "ruby": Language.RUBY, "php": Language.PHP, "html": Language.HTML, "markdown": Language.MARKDOWN,
}


@dataclass
class Chunk:
    ordinal: int
    content: str
    token_count: int
    line_start: int | None = None
    line_end: int | None = None
    page_number: int | None = None
    section: str | None = None


def estimate_tokens(text: str) -> int:
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


def _splitter(language: str | None) -> RecursiveCharacterTextSplitter:
    kwargs = dict(chunk_size=MAX_CHARS, chunk_overlap=OVERLAP_CHARS, add_start_index=True)
    lang = _LANGUAGES.get(language or "")
    if lang is not None:
        return RecursiveCharacterTextSplitter.from_language(lang, **kwargs)
    return RecursiveCharacterTextSplitter(**kwargs)


def _split_oversized(seg: Segment, language: str | None) -> list[Segment]:
    pieces: list[Segment] = []
    cursor = 0
    for doc in _splitter(language).create_documents([seg.text]):
        start = doc.metadata.get("start_index", -1)
        if start < 0:
            start = cursor
        cursor = start + len(doc.page_content)
        line_start = line_end = None
        if seg.line_start is not None:
            line_start = seg.line_start + seg.text.count("\n", 0, start)
            line_end = line_start + doc.page_content.count("\n")
        pieces.append(Segment(doc.page_content, line_start, line_end, seg.page, seg.section))
    return pieces


def _pack(segments: list[Segment]) -> list[Segment]:
    """Merge adjacent small segments (config-file keys) so each chunk is a useful size."""
    out: list[Segment] = []
    run: list[Segment] = []

    def flush() -> None:
        if not run:
            return
        first, last = run[0], run[-1]
        names = [s.section for s in run if s.section]
        section = None
        if names:
            section = names[0] if len(set(names)) == 1 else f"{names[0]} – {names[-1]}"
        text = run[0].text
        for prev, seg in zip(run, run[1:]):
            gap = (seg.line_start - prev.line_end) if seg.line_start and prev.line_end else 1
            text += "\n" * gap + seg.text  # keep skipped blank lines so content matches the line range
        out.append(Segment(text, first.line_start, last.line_end, first.page, section))
        run.clear()

    size = 0
    for seg in segments:
        if run and size + len(seg.text) + 1 > MAX_CHARS:
            flush()
            size = 0
        run.append(seg)
        size += len(seg.text) + 1
    flush()
    return out


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    segments = _pack(doc.segments) if doc.pack else doc.segments
    chunks: list[Chunk] = []
    for seg in segments:
        parts = [seg] if len(seg.text) <= MAX_CHARS else _split_oversized(seg, doc.language)
        for part in parts:
            text = part.text.replace("\x00", "")  # Postgres text columns cannot store NUL
            if not text.strip():
                continue
            chunks.append(Chunk(
                ordinal=len(chunks), content=text, token_count=estimate_tokens(text),
                line_start=part.line_start, line_end=part.line_end, page_number=part.page, section=part.section,
            ))
    return chunks
