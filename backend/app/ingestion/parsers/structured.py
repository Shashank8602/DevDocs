"""JSON and YAML, split at top-level keys; CSV, split into groups of rows with the header repeated."""
import csv
import io
import json
import re

from .base import ParsedDocument, ParseError, Segment, decode, whole_file

CSV_GROUP_CHARS = 3000


def _by_offsets(text: str, starts: list[tuple[int, str]], language: str) -> ParsedDocument:
    """Segments from (character offset, key) markers; text before the first marker is its own segment."""
    if not starts:
        return whole_file(text, language, pack=True)
    bounds: list[tuple[int, str | None]] = list(starts)
    if starts[0][0] > 0:
        bounds.insert(0, (0, None))
    segments = []
    for n, (offset, key) in enumerate(bounds):
        end = bounds[n + 1][0] if n + 1 < len(bounds) else len(text)
        body = text[offset:end].rstrip()
        if not body.strip():
            continue
        first = text.count("\n", 0, offset) + 1
        segments.append(Segment(body, first, first + body.count("\n"), section=key))
    return ParsedDocument(segments, language, pack=True)


def _json_keys(text: str) -> list[tuple[int, str]]:
    """Offsets of the keys of the top-level object. Empty when the root is not an object."""
    keys: list[tuple[int, str]] = []
    depth, i, n = 0, 0, len(text)
    prev = ""  # previous significant character
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if depth == 1 and prev in ("{", ","):
                keys.append((i, text[i + 1 : j]))
            i, prev = j + 1, '"'
            continue
        if ch in "{[":
            if depth == 0 and ch == "[":
                return []
            depth += 1
        elif ch in "}]":
            depth -= 1
        if not ch.isspace():
            prev = ch
        i += 1
    return keys


def parse_json(data: bytes) -> ParsedDocument:
    text = decode(data)
    try:
        json.loads(text)
    except ValueError:
        return whole_file(text, "json")  # invalid JSON is still searchable text
    return _by_offsets(text, _json_keys(text), "json")


_YAML_TOP = re.compile(r"^([A-Za-z0-9_.\"'][^:\n]*):(?:\s|$)", re.M)


def parse_yaml(data: bytes) -> ParsedDocument:
    text = decode(data)
    starts = [(m.start(), m.group(1).strip().strip("\"'")) for m in _YAML_TOP.finditer(text)]
    return _by_offsets(text, starts, "yaml")


def parse_csv(data: bytes) -> ParsedDocument:
    text = decode(data)
    if not text.strip():
        return ParsedDocument([])
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    reader = csv.reader(io.StringIO(text))
    records: list[tuple[int, int]] = []  # (first line, last line) of each record, 1-based
    try:
        prev = 0
        for _ in reader:
            records.append((prev + 1, reader.line_num))
            prev = reader.line_num
    except csv.Error as exc:
        raise ParseError(f"malformed CSV: {exc}") from exc
    header_end = records[0][1]
    header = "\n".join(lines[:header_end])

    segments: list[Segment] = []
    group: list[tuple[int, int]] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if group:
            lo, hi = group[0][0], group[-1][1]
            segments.append(Segment(header + "\n" + "\n".join(lines[lo - 1 : hi]), lo, hi))
        group.clear()
        size = 0

    for lo, hi in records[1:]:
        length = sum(len(x) + 1 for x in lines[lo - 1 : hi])
        if group and size + length > CSV_GROUP_CHARS:
            flush()
        group.append((lo, hi))
        size += length
    flush()
    if not segments:  # header only
        segments.append(Segment(header, 1, header_end))
    return ParsedDocument(segments)
