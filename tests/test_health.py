from httpx import AsyncClient

from app.core.config import Settings


async def test_liveness(client: AsyncClient, settings: Settings) -> None:
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["instance"] == settings.instance_id
    assert response.headers["X-Served-By"] == settings.instance_id


async def test_readiness(client: AsyncClient, settings: Settings) -> None:
    response = await client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["instance"] == settings.instance_id


async def test_root(client: AsyncClient, settings: Settings) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == settings.app_name
    assert body["instance"] == settings.instance_id
    assert body["docs"].startswith("/")
