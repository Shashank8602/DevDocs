"""Word documents, split by heading. Tables are flattened to "cell | cell" rows."""
import io

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import ParsedDocument, ParseError, Segment


def _heading_level(paragraph: Paragraph) -> int | None:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name == "Title":
        return 1
    if name.startswith("Heading ") and name.split(" ", 1)[1].isdigit():
        return int(name.split(" ", 1)[1])
    return None


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        doc = Document(io.BytesIO(data))
        body = list(doc.element.body.iterchildren())
    except Exception as exc:
        raise ParseError(f"unreadable DOCX: {exc!r}") from exc

    segments: list[Segment] = []
    path: list[tuple[int, str]] = []
    buf: list[str] = []
    section: str | None = None

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            segments.append(Segment(text, section=section))
        buf.clear()

    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            level = _heading_level(para)
            if level is not None:
                flush()
                while path and path[-1][0] >= level:
                    path.pop()
                path.append((level, text))
                section = " > ".join(t for _, t in path)
            buf.append(text)
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    buf.append(" | ".join(cells))
    flush()
    return ParsedDocument(segments)
