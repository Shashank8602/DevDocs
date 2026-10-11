import io

from app.ingestion.change_detection import diff
from app.ingestion.hashing import BLOCK_SIZE, copy_and_hash


def test_deleted_when_path_missing_from_new():
    assert diff({"a.py": "h1"}, {}).deleted == ["a.py"]


def test_created_when_path_not_in_old():
    assert diff({}, {"a.py": "h1"}).created == ["a.py"]


def test_modified_when_hash_differs():
    assert diff({"a.py": "h1"}, {"a.py": "h2"}).modified == ["a.py"]


def test_unchanged_when_hash_equal():
    assert diff({"a.py": "h1"}, {"a.py": "h1"}).unchanged == ["a.py"]


def test_move_is_delete_plus_create():
    d = diff({"utils.py": "h3"}, {"sub/utils.py": "h3"})
    assert (d.deleted, d.created, d.modified, d.unchanged) == (["utils.py"], ["sub/utils.py"], [], [])


def test_unhashed_stored_file_counts_as_modified():
    assert diff({"a.py": None}, {"a.py": "h1"}).modified == ["a.py"]


def test_spec_reingestion_example():
    old = {"auth.py": "h1", "database.py": "h2", "utils.py": "h3", "main.py": "h4"}
    new = {"auth.py": "h1b", "security.py": "h5", "sub/utils.py": "h3", "main.py": "h4"}
    d = diff(old, new)
    assert d.modified == ["auth.py"]
    assert d.deleted == ["database.py", "utils.py"]
    assert d.created == ["security.py", "sub/utils.py"]
    assert d.unchanged == ["main.py"]


def test_hash_streams_in_blocks_and_matches_sha256():
    import hashlib

    data = b"x" * (BLOCK_SIZE * 2 + 5)
    out = io.BytesIO()
    digest, size = copy_and_hash(io.BytesIO(data), out)
    assert digest == hashlib.sha256(data).hexdigest() and size == len(data) and out.getvalue() == data
