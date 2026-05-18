from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import SessionDep
from app.crud import item as crud
from app.schemas.item import ItemCreate, ItemPage, ItemRead, ItemUpdate

router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=ItemPage, summary="List items with pagination")
async def list_items(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemPage:
    items, total = await crud.list_items(session, limit=limit, offset=offset)
    return ItemPage(
        items=[ItemRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item",
)
async def create_item(payload: ItemCreate, session: SessionDep) -> ItemRead:
    item = await crud.create_item(session, payload)
    return ItemRead.model_validate(item)


@router.get("/{item_id}", response_model=ItemRead, summary="Fetch one item by id")
async def get_item(item_id: int, session: SessionDep) -> ItemRead:
    item = await crud.get_item(session, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item not found")
    return ItemRead.model_validate(item)


@router.patch("/{item_id}", response_model=ItemRead, summary="Partially update an item")
async def update_item(item_id: int, payload: ItemUpdate, session: SessionDep) -> ItemRead:
    item = await crud.get_item(session, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item not found")
    updated = await crud.update_item(session, item, payload)
    return ItemRead.model_validate(updated)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item",
)
async def delete_item(item_id: int, session: SessionDep) -> Response:
    affected = await crud.delete_item(session, item_id)
    if affected == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
