"""File-type parsers. Every parser returns a ParsedDocument, so chunking never cares about the format."""
from pathlib import PurePosixPath

from .base import ParsedDocument, ParseError, Segment
from .code import parse_code
from .docx import parse_docx
from .markdown import parse_markdown
from .pdf import parse_pdf
from .structured import parse_csv, parse_json, parse_yaml
from .text import parse_text

__all__ = ["ParsedDocument", "ParseError", "Segment", "parse"]


def parse(data: bytes, file_path: str, file_type: str, language: str | None) -> ParsedDocument:
    ext = PurePosixPath(file_path).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(data)
    if ext == ".docx":
        return parse_docx(data)
    if ext == ".md":
        return parse_markdown(data)
    if ext == ".csv":
        return parse_csv(data)
    if ext == ".json":
        return parse_json(data)
    if ext in (".yaml", ".yml"):
        return parse_yaml(data)
    if file_type == "source":
        return parse_code(data, language)  # includes .html: split by tags, source line numbers kept
    return parse_text(data, language)
