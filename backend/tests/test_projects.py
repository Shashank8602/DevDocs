import uuid


async def _create(client, name="E-Commerce API", **extra):
    res = await client.post("/projects", json={"name": name, **extra})
    assert res.status_code == 201, res.text
    return res.json()


async def test_create_and_get(client):
    created = await _create(client, description="Shop backend")
    assert created["status"] == "empty"
    assert created["file_count"] == 0
    assert created["description"] == "Shop backend"

    res = await client.get(f"/projects/{created['id']}")
    assert res.status_code == 200
    assert res.json()["name"] == "E-Commerce API"


async def test_list_returns_all_projects(client):
    await _create(client, "E-Commerce API")
    await _create(client, "Banking App")
    res = await client.get("/projects")
    assert {p["name"] for p in res.json()} == {"E-Commerce API", "Banking App"}


async def test_rename_keeps_project_id(client):
    created = await _create(client, "Banking App")
    res = await client.patch(f"/projects/{created['id']}", json={"name": "Banking Platform"})
    assert res.status_code == 200
    assert res.json()["id"] == created["id"]
    assert res.json()["name"] == "Banking Platform"
    assert res.json()["created_at"] == created["created_at"]

    listed = (await client.get("/projects")).json()
    assert [p["id"] for p in listed] == [created["id"]]


async def test_delete_removes_only_that_project(client):
    keep = await _create(client, "E-Commerce API")
    drop = await _create(client, "Banking App")

    assert (await client.delete(f"/projects/{drop['id']}")).status_code == 204
    assert (await client.get(f"/projects/{drop['id']}")).status_code == 404
    assert [p["id"] for p in (await client.get("/projects")).json()] == [keep["id"]]


async def test_unknown_project_is_404(client):
    missing = uuid.uuid4()
    assert (await client.get(f"/projects/{missing}")).status_code == 404
    assert (await client.patch(f"/projects/{missing}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/projects/{missing}")).status_code == 404


async def test_name_is_trimmed_and_validated(client):
    assert (await _create(client, "  Padded  "))["name"] == "Padded"
    assert (await client.post("/projects", json={"name": "   "})).status_code == 422
    assert (await client.post("/projects", json={"name": "x" * 101})).status_code == 422
    assert (await client.post("/projects", json={})).status_code == 422
