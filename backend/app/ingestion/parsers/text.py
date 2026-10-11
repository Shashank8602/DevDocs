"""Plain text and markup files (TXT, TOML, XML): one segment, split by paragraph."""
from .base import ParsedDocument, decode, whole_file


def parse_text(data: bytes, language: str | None = None) -> ParsedDocument:
    return whole_file(decode(data), language)
