import pytest

from app.config import settings
from app.ingestion import rules


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


async def _project(client):
    return (await client.post("/projects", json={"name": "Demo"})).json()["id"]


async def _upload(client, pid, items):
    """items: [(relative_path, bytes)]"""
    files = [("files", (p.rsplit("/", 1)[-1], data)) for p, data in items]
    return await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})


async def test_rules_endpoint_serves_the_shared_rules(client):
    body = (await client.get("/upload-rules")).json()
    assert ".env" in body["ignored_names"] and "node_modules" in body["ignored_names"]
    assert body["max_file_bytes"] == 20 * 1024 * 1024
    assert body["max_total_bytes"] == 50 * 1024 * 1024
    assert ".py" in body["extensions"]


async def test_upload_keeps_paths_and_skips_bad_files_with_report(client, tmp_path):
    pid = await _project(client)
    res = await _upload(client, pid, [
        ("demo/app/auth/jwt.py", b"print(1)\n"),
        ("demo/README.md", b"# hi"),
        ("demo/node_modules/lib/index.js", b"x"),
        ("demo/.env", b"SECRET=1"),
        ("demo/logo.png", b"\x89PNG"),
        ("../evil.py", b"x"),
    ])
    assert res.status_code == 200
    report = res.json()
    assert (report["accepted"], report["rejected"]) == (2, 4)
    reasons = {r["path"]: r["reason"] for r in report["results"] if r["status"] == "rejected"}
    assert reasons == {
        "demo/node_modules/lib/index.js": "ignored",
        "demo/.env": "ignored",
        "demo/logo.png": "unsupported_type",
        "../evil.py": "invalid_path",
    }

    listed = (await client.get(f"/projects/{pid}/files")).json()
    assert sorted(f["file_path"] for f in listed) == ["demo/README.md", "demo/app/auth/jwt.py"]
    jwt = next(f for f in listed if f["file_name"] == "jwt.py")
    assert (jwt["file_type"], jwt["language"], jwt["status"]) == ("source", "python", "pending")
    assert (tmp_path / "projects" / pid / "demo/app/auth/jwt.py").read_bytes() == b"print(1)\n"
    assert not (tmp_path / "evil.py").exists()
    assert (await client.get(f"/projects/{pid}")).json()["file_count"] == 2


async def test_server_enforces_size_limits(client, monkeypatch):
    monkeypatch.setattr(rules, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(rules, "MAX_TOTAL_BYTES", 15)
    pid = await _project(client)
    res = await _upload(client, pid, [
        ("a.py", b"x" * 11),  # over per-file limit
        ("b.py", b"x" * 8),
        ("c.py", b"x" * 8),  # would push total to 16
    ])
    results = {r["path"]: r.get("reason") for r in res.json()["results"]}
    assert results == {"a.py": "too_large", "b.py": None, "c.py": "total_limit_exceeded"}


async def test_reupload_same_path_does_not_duplicate(client, ingest):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1")])
    await ingest(pid)
    await _upload(client, pid, [("a.py", b"22")])
    listed = (await client.get(f"/projects/{pid}/files")).json()
    assert len(listed) == 1 and listed[0]["file_size"] == 2


async def test_content_and_delete(client, tmp_path):
    pid = await _project(client)
    await _upload(client, pid, [("page.html", b"<h1>x</h1>"), ("a.py", b"1")])
    files = {f["file_name"]: f for f in (await client.get(f"/projects/{pid}/files")).json()}

    html = await client.get(f"/files/{files['page.html']['id']}/content")
    assert html.content == b"<h1>x</h1>"
    assert html.headers["content-type"].startswith("text/plain")  # shown as source, not rendered

    fid = files["a.py"]["id"]
    assert (await client.delete(f"/files/{fid}")).status_code == 204
    assert (await client.get(f"/files/{fid}/content")).status_code == 404
    assert not (tmp_path / "projects" / pid / "a.py").exists()
    assert [f["file_name"] for f in (await client.get(f"/projects/{pid}/files")).json()] == ["page.html"]


async def test_project_delete_removes_files_on_disk(client, tmp_path):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1")])
    await client.delete(f"/projects/{pid}")
    assert not (tmp_path / "projects" / pid).exists()


async def test_upload_unknown_project_404(client):
    res = await _upload(client, "00000000-0000-0000-0000-000000000000", [("a.py", b"1")])
    assert res.status_code == 404


async def test_reupload_reports_created_modified_deleted_unchanged(client, tmp_path, ingest):
    pid = await _project(client)
    await _upload(client, pid, [
        ("auth.py", b"v1"), ("database.py", b"db"), ("utils.py", b"u"), ("main.py", b"m"),
    ])
    await ingest(pid)
    res = await _upload(client, pid, [
        ("auth.py", b"v2"), ("security.py", b"s"), ("sub/utils.py", b"u"), ("main.py", b"m"),
    ])
    r = res.json()
    assert r["modified"] == ["auth.py"]
    assert r["deleted"] == ["database.py", "utils.py"]
    assert r["created"] == ["security.py", "sub/utils.py"]
    assert r["unchanged"] == ["main.py"]
    listed = [f["file_path"] for f in (await client.get(f"/projects/{pid}/files")).json()]
    assert listed == ["auth.py", "main.py", "security.py", "sub/utils.py"]
    root = tmp_path / "projects" / pid
    assert not (root / "database.py").exists() and not (root / "utils.py").exists()
    assert (root / "auth.py").read_bytes() == b"v2"
    assert not [p for p in root.iterdir() if p.name.startswith(".staging")]


async def test_unchanged_file_keeps_its_status_modified_goes_pending(client, ingest):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1"), ("b.py", b"2")])
    await ingest(pid)
    await _upload(client, pid, [("a.py", b"1"), ("b.py", b"changed")])
    status = {f["file_path"]: f["status"] for f in (await client.get(f"/projects/{pid}/files")).json()}
    assert status == {"a.py": "ready", "b.py": "pending"}


async def test_upload_with_nothing_usable_deletes_nothing(client, ingest):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1")])
    await ingest(pid)
    res = await _upload(client, pid, [("logo.png", b"x")])
    assert res.json()["deleted"] == []
    assert len((await client.get(f"/projects/{pid}/files")).json()) == 1


async def test_replace_single_file_only_touches_that_file(client, tmp_path, ingest):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1"), ("b.py", b"2")])
    await ingest(pid)
    files = {f["file_name"]: f["id"] for f in (await client.get(f"/projects/{pid}/files")).json()}

    same = await client.put(f"/files/{files['a.py']}", files={"file": ("a.py", b"1")})
    assert same.json()["changed"] is False

    diffd = await client.put(f"/files/{files['a.py']}", files={"file": ("whatever.py", b"new")})
    assert diffd.json()["changed"] is True
    assert (tmp_path / "projects" / pid / "a.py").read_bytes() == b"new"
    assert (tmp_path / "projects" / pid / "b.py").read_bytes() == b"2"
    assert len((await client.get(f"/projects/{pid}/files")).json()) == 2


async def test_overview_counts_types_and_languages(client):
    pid = await _project(client)
    await _upload(client, pid, [("a.py", b"1"), ("b.py", b"2"), ("c.ts", b"3"), ("README.md", b"# x")])
    o = (await client.get(f"/projects/{pid}/overview")).json()
    assert o["file_count"] == 4
    assert o["by_type"]["source"] == 3 and o["by_type"]["doc"] == 1 and o["by_type"]["other"] == 0
    assert o["languages"][0] == {"language": "python", "count": 2}
    assert o["last_updated"] is not None

