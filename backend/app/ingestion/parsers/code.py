"""Source code. Python is split at function/class boundaries with `ast`; other languages
are one segment and the chunker splits them with a language-aware splitter."""
import ast

from .base import ParsedDocument, Segment, decode, whole_file


def parse_code(data: bytes, language: str | None) -> ParsedDocument:
    text = decode(data)
    if language == "python":
        try:
            segments = _python_segments(text)
        except (SyntaxError, ValueError, RecursionError):
            return whole_file(text, language)  # not valid Python: fall back to generic splitting
        return ParsedDocument(segments, language)
    return whole_file(text, language)


def _python_segments(text: str) -> list[Segment]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    tree = ast.parse(text)
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return _block(lines, defs, 1, len(lines), None)


def _start_line(lines: list[str], node) -> int:
    """First line of a definition, including decorators and the comment block right above it."""
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    while start > 1 and lines[start - 2].lstrip().startswith("#"):
        start -= 1
    return start


def _emit(lines: list[str], lo: int, hi: int, section: str | None, out: list[Segment]) -> None:
    while lo <= hi and not lines[lo - 1].strip():  # leave surrounding blank lines out of the range
        lo += 1
    while hi >= lo and not lines[hi - 1].strip():
        hi -= 1
    if lo <= hi:
        out.append(Segment("\n".join(lines[lo - 1 : hi]), lo, hi, section=section))


def _block(lines: list[str], nodes: list, lo: int, hi: int, prefix: str | None) -> list[Segment]:
    """Segments for lines lo..hi: each definition on its own, module-level code in between."""
    out: list[Segment] = []
    cursor = lo
    for node in nodes:
        start, end = max(_start_line(lines, node), cursor), node.end_lineno
        _emit(lines, cursor, start - 1, prefix, out)
        name = f"{prefix}.{node.name}" if prefix else node.name
        if isinstance(node, ast.ClassDef) and end - start > 60:
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out.extend(_block(lines, methods, start, end, name))  # long class: header + one per method
        else:
            _emit(lines, start, end, name, out)
        cursor = end + 1
    _emit(lines, cursor, hi, prefix, out)
    return out
