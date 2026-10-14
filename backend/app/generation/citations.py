"""Citations are built here, in application code. The LLM never writes them, so they cannot be invented.

Rules (spec 7): sources follow retrieval order, chunks of one file are grouped under that file (at the
position of its best chunk), each line range stays separate (never merged or re-sorted), exact duplicate
ranges are removed. Source files show their line ranges; every other file type shows the path only."""
from app.retrieval.selection import Candidate

SHOWS_LINES = "source"


def _label(path: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return path
    parts = [f"{a}" if a == b else f"{a}–{b}" for a, b in ranges]
    return f"{path} — {'Line' if len(ranges) == 1 and ranges[0][0] == ranges[0][1] else 'Lines'} {', '.join(parts)}"


def build_citations(chunks: list[Candidate]) -> list[dict]:
    """`chunks` are the final context chunks in retrieval order. Returns the JSON stored in
    `messages.sources_json`: [{file_id, file_path, file_type, ranges: [[start, end], ...], label}]."""
    order: list[str] = []
    by_file: dict[str, dict] = {}
    for c in chunks:
        src = by_file.get(c.file_id)
        if src is None:
            src = by_file[c.file_id] = {
                "file_id": c.file_id, "file_path": c.file_path, "file_type": c.file_type, "ranges": [],
            }
            order.append(c.file_id)
        if c.file_type == SHOWS_LINES and c.line_start is not None and c.line_end is not None:
            rng = [c.line_start, c.line_end]
            if rng not in src["ranges"]:
                src["ranges"].append(rng)
    out = []
    for fid in order:
        src = by_file[fid]
        src["label"] = _label(src["file_path"], [tuple(r) for r in src["ranges"]])
        out.append(src)
    return out
