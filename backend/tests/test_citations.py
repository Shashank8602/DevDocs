"""Citation formatting rules (pure function over the final chunks)."""
from app.generation.citations import build_citations
from app.retrieval.selection import Candidate


def chunk(file_id, path, start=None, end=None, file_type="source"):
    return Candidate(
        chunk_id=f"{file_id}-{start}", file_id=file_id, file_path=path, content="x", line_start=start,
        line_end=end, page_number=None, section=None, token_count=10, rrf_score=1.0, bm25_rank=None,
        vector_rank=None, file_type=file_type,
    )


def test_groups_by_file_keeps_ranges_separate_and_in_retrieval_order():
    out = build_citations([
        chunk("a", "backend/auth.py", 35, 48), chunk("b", "backend/jwt.py", 1, 9), chunk("a", "backend/auth.py", 10, 20),
    ])
    assert [s["file_path"] for s in out] == ["backend/auth.py", "backend/jwt.py"]  # file order = first appearance
    assert out[0]["ranges"] == [[35, 48], [10, 20]]  # never merged, never re-sorted
    assert out[0]["label"] == "backend/auth.py — Lines 35–48, 10–20"
    assert out[1]["label"] == "backend/jwt.py — Lines 1–9"


def test_exact_duplicate_ranges_are_removed():
    out = build_citations([chunk("a", "a.py", 1, 5), chunk("a", "a.py", 1, 5), chunk("a", "a.py", 1, 6)])
    assert out[0]["ranges"] == [[1, 5], [1, 6]]


def test_non_source_files_show_the_path_only():
    out = build_citations([chunk("r", "README.md", 1, 40, file_type="doc"), chunk("c", "cfg.yaml", 1, 3, file_type="other")])
    assert [(s["ranges"], s["label"]) for s in out] == [([], "README.md"), ([], "cfg.yaml")]


def test_single_line_range_reads_as_line():
    assert build_citations([chunk("a", "a.py", 7, 7)])[0]["label"] == "a.py — Line 7"


def test_no_chunks_no_sources():
    assert build_citations([]) == []
