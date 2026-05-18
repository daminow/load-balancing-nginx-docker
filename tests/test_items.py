from httpx import AsyncClient


async def test_create_get_item(client: AsyncClient) -> None:
    payload = {"name": "Widget", "description": "Small widget", "quantity": 5}
    created = await client.post("/api/v1/items", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["id"] > 0
    assert body["name"] == payload["name"]
    assert body["quantity"] == payload["quantity"]

    fetched = await client.get(f"/api/v1/items/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


async def test_create_validation(client: AsyncClient) -> None:
    bad = await client.post("/api/v1/items", json={"name": "", "quantity": -1})
    assert bad.status_code == 422


async def test_list_pagination(client: AsyncClient) -> None:
    for i in range(5):
        await client.post("/api/v1/items", json={"name": f"item-{i}", "quantity": i})

    page = await client.get("/api/v1/items", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    data = page.json()
    assert data["total"] == 5
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert len(data["items"]) == 2


async def test_update_item(client: AsyncClient) -> None:
    created = await client.post("/api/v1/items", json={"name": "stale", "quantity": 1})
    item_id = created.json()["id"]

    updated = await client.patch(f"/api/v1/items/{item_id}", json={"name": "fresh", "quantity": 9})
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "fresh"
    assert body["quantity"] == 9


async def test_delete_item(client: AsyncClient) -> None:
    created = await client.post("/api/v1/items", json={"name": "to-delete", "quantity": 0})
    item_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/items/{item_id}")
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/items/{item_id}")
    assert missing.status_code == 404


async def test_delete_unknown(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/items/999999")
    assert response.status_code == 404
