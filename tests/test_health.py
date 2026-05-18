from httpx import AsyncClient


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["instance"] == "test-instance"
    assert response.headers["X-Served-By"] == "test-instance"


async def test_readiness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["instance"] == "test-instance"


async def test_root(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "load-balancing-app"
    assert body["docs"] == "/docs"
