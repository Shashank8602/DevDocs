"""Background ingestion: pending -> processing -> ready | failed, derived project status, retry, replace."""
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Chunk, File
from tests.test_chunking import make_pdf

PY = "import os\n\n\ndef login(user):\n    return user\n\n\ndef logout():\n    pass\n"


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


async def _project(client):
    return (await client.post("/projects", json={"name": "Demo"})).json()["id"]


async def _upload(client, pid, items):
    files = [("files", (p.rsplit("/", 1)[-1], data)) for p, data in items]
    return await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})


async def _files(client, pid):
    return {f["file_path"]: f for f in (await client.get(f"/projects/{pid}/files")).json()}


async def _status(client, pid):
    return (await client.get(f"/projects/{pid}")).json()["status"]


async def test_upload_queues_files_and_worker_moves_them_to_ready(client, ingest, _no_background_worker):
    pid = await _project(client)
    await _upload(client, pid, [("d/auth.py", PY.encode()), ("d/README.md", b"# Title\n\nbody\n")])
    assert _no_background_worker == [__import__("uuid").UUID(pid)]
    assert {f["status"] for f in (await _files(client, pid)).values()} == {"pending"}
    assert await _status(client, pid) == "processing"

    await ingest(pid)
    assert {f["status"] for f in (await _files(client, pid)).values()} == {"ready"}
    assert await _status(client, pid) == "ready"
    assert (await client.get(f"/projects/{pid}")).json()["last_ingested_at"] is not None


async def test_chunks_are_stored_with_line_ranges_and_inspector_is_dev_only(client, ingest, monkeypatch):
    pid = await _project(client)
    await _upload(client, pid, [("auth.py", PY.encode()), ("README.md", b"# A\n\ntext\n\n## B\n\nmore\n")])
    await ingest(pid)
    files = await _files(client, pid)

    monkeypatch.setattr(settings, "dev_mode", True)
    py = (await client.get(f"/projects/{pid}/files/{files['auth.py']['id']}/chunks")).json()
    assert [(c["section"], c["line_start"], c["line_end"]) for c in py] == [
        (None, 1, 1), ("login", 4, 5), ("logout", 8, 9),
    ]
    md = (await client.get(f"/projects/{pid}/files/{files['README.md']['id']}/chunks")).json()
    assert [c["section"] for c in md] == ["A", "A > B"]

    other = await _project(client)
    wrong = await client.get(f"/projects/{other}/files/{files['auth.py']['id']}/chunks")
    assert wrong.status_code == 404  # a file is only reachable through its own project

    monkeypatch.setattr(settings, "dev_mode", False)
    hidden = await client.get(f"/projects/{pid}/files/{files['auth.py']['id']}/chunks")
    assert hidden.status_code == 404


async def test_corrupt_pdf_fails_alone_then_replace_turns_it_ready(client, ingest, sessions):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", PY.encode()), ("docs/bad.pdf", b"this is not really a pdf")])
    await ingest(pid)

    files = await _files(client, pid)
    assert files["a.py"]["status"] == "ready" and files["docs/bad.pdf"]["status"] == "failed"
    assert "error_message" not in files["docs/bad.pdf"]  # internal only
    assert await _status(client, pid) == "ready"  # one bad file never fails the project

    res = await client.put(
        f"/files/{files['docs/bad.pdf']['id']}", files={"file": ("bad.pdf", make_pdf(["Real content."]))}
    )
    assert res.json()["changed"] is True
    assert (await _files(client, pid))["docs/bad.pdf"]["status"] == "pending"
    await ingest(pid)
    assert (await _files(client, pid))["docs/bad.pdf"]["status"] == "ready"
    async with sessions() as s:
        pages = (await s.scalars(select(Chunk.page_number).where(Chunk.file_id == files["docs/bad.pdf"]["id"]))).all()
    assert pages == [1]


async def test_all_files_failed_makes_project_failed_and_retry_requeues(client, ingest, _no_background_worker, sessions):
    pid = await _project(client)
    await _upload(client, pid, [("bad.pdf", b"nope")])
    await ingest(pid)
    assert await _status(client, pid) == "failed"
    fid = (await _files(client, pid))["bad.pdf"]["id"]

    _no_background_worker.clear()
    assert (await client.post(f"/files/{fid}/retry")).status_code == 202
    assert len(_no_background_worker) == 1
    assert await _status(client, pid) == "processing"
    await ingest(pid)
    assert await _status(client, pid) == "failed"

    async with sessions() as s:
        row = await s.get(File, __import__("uuid").UUID(fid))
        row.status = "ready"
        await s.commit()
    assert (await client.post(f"/files/{fid}/retry")).status_code == 409  # only failed files can be retried


async def test_upload_is_blocked_while_ingestion_runs(client, ingest):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", PY.encode())])
    again = await _upload(client, pid, [("a.py", b"print(1)\n")])
    assert again.status_code == 409
    await ingest(pid)
    assert (await _upload(client, pid, [("a.py", b"print(1)\n")])).status_code == 200


async def test_modified_file_gets_fresh_chunks_and_deleted_file_loses_them(client, ingest, sessions):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", PY.encode()), ("b.py", b"x = 1\n")])
    await ingest(pid)
    await _upload(client, pid, [("a.py", b"def only():\n    pass\n"), ("b.py", b"x = 1\n")])
    await ingest(pid)
    files = await _files(client, pid)
    async with sessions() as s:
        sections = (await s.scalars(select(Chunk.section).where(Chunk.file_id == files["a.py"]["id"]))).all()
        assert sections == ["only"]  # old chunks were replaced, not appended

    await client.delete(f"/files/{files['b.py']['id']}")
    async with sessions() as s:
        assert (await s.scalars(select(Chunk).where(Chunk.file_id == files["b.py"]["id"]))).all() == []
    await client.delete(f"/files/{files['a.py']['id']}")
    assert await _status(client, pid) == "empty"


async def test_file_deleted_mid_processing_is_ignored(client, sessions):
    import uuid
    from app.ingestion import worker

    pid = await _project(client)
    await _upload(client, pid, [("a.py", PY.encode())])
    fid = (await _files(client, pid))["a.py"]["id"]
    async with sessions() as s:  # the file is picked up ...
        (await s.get(File, uuid.UUID(fid))).status = "processing"
        await s.commit()
    await client.delete(f"/files/{fid}")  # ... then deleted before the result is stored
    await worker._ingest_file(sessions, uuid.UUID(pid), uuid.UUID(fid), "a.py", "source", "python")
    async with sessions() as s:
        assert (await s.scalars(select(Chunk))).all() == []


async def test_active_ingestion_lists_only_processing_projects(client, ingest):
    pid = await _project(client)
    await _project(client)
    await _upload(client, pid, [("a.py", PY.encode()), ("b.py", b"x = 1\n")])
    active = (await client.get("/ingestion/active")).json()
    assert [(a["project_id"], a["project_name"], a["remaining"], a["total"]) for a in active] == [(pid, "Demo", 2, 2)]
    await ingest(pid)
    assert (await client.get("/ingestion/active")).json() == []


async def test_config_reports_dev_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    assert (await client.get("/config")).json() == {"dev_mode": True}
