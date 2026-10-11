import httpx
import pytest

from app import main


@pytest.mark.asyncio
async def test_health_reports_each_dependency(monkeypatch):
    async def ok():
        return None

    async def down():
        raise ConnectionError("down")

    monkeypatch.setattr(main, "ping_postgres", ok)
    monkeypatch.setattr(main, "ping_elasticsearch", down)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"postgres": "ok", "elasticsearch": "error"}
