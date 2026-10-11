"""Markdown, split by heading. `section` is the heading path, e.g. "Setup > Install"."""
from markdown_it import MarkdownIt

from .base import ParsedDocument, Segment, decode


def parse_markdown(data: bytes) -> ParsedDocument:
    text = decode(data)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    tokens = MarkdownIt("commonmark").parse(text)

    headings: list[tuple[int, int, str]] = []  # (0-based line, level, title)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            headings.append((tok.map[0], int(tok.tag[1]), tokens[i + 1].content.strip()))

    segments: list[Segment] = []
    path: list[tuple[int, str]] = []

    def add(start: int, end: int, section: str | None) -> None:
        body = "\n".join(lines[start:end])
        if body.strip():
            segments.append(Segment(body, start + 1, end, section=section))

    add(0, headings[0][0] if headings else len(lines), None)
    for n, (line, level, title) in enumerate(headings):
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        add(line, end, " > ".join(t for _, t in path))
    return ParsedDocument(segments, "markdown")
