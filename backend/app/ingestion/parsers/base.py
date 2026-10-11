"""The common document format every parser produces, and the shared text helpers."""
from dataclasses import dataclass


class ParseError(Exception):
    """The file could not be turned into text. Recorded on the file as an internal error."""


@dataclass
class Segment:
    """A natural unit of a document (a function, a heading section, a page, a group of rows)."""

    text: str
    line_start: int | None = None  # 1-based, inclusive; None for formats without lines (PDF, DOCX)
    line_end: int | None = None
    page: int | None = None  # 1-based; PDFs only
    section: str | None = None  # heading path, or function/class name


@dataclass
class ParsedDocument:
    segments: list[Segment]
    language: str | None = None  # drives language-aware splitting of oversized segments
    pack: bool = False  # merge adjacent small segments into chunks (config-like files)


def decode(data: bytes) -> str:
    """Decode text with CRLF/CR normalised to LF, so line numbers match the viewer."""
    if b"\x00" in data[:8192]:
        raise ParseError("binary content")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def line_count(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def whole_file(text: str, language: str | None = None, **kwargs) -> ParsedDocument:
    """A single segment covering every line; the chunker splits it if it is too large."""
    if not text.strip():
        return ParsedDocument([], language, **kwargs)
    return ParsedDocument([Segment(text, 1, line_count(text))], language, **kwargs)
