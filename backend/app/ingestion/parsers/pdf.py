"""PDF, one segment per page. Pages without extractable text are skipped."""
import io

from pypdf import PdfReader

from .base import ParsedDocument, ParseError, Segment


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(n, (page.extract_text() or "").strip()) for n, page in enumerate(reader.pages, start=1)]
    except Exception as exc:  # pypdf raises many different types for damaged files
        raise ParseError(f"unreadable PDF: {exc!r}") from exc
    segments = [Segment(text, page=n) for n, text in pages if text]
    if not segments:
        raise ParseError("PDF has no extractable text")
    return ParsedDocument(segments)
