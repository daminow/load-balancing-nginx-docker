from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import Item
from app.schemas.item import ItemCreate, ItemUpdate


async def create_item(session: AsyncSession, payload: ItemCreate) -> Item:
    item = Item(
        name=payload.name,
        description=payload.description,
        quantity=payload.quantity,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_item(session: AsyncSession, item_id: int) -> Item | None:
    return await session.get(Item, item_id)


async def list_items(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[Item], int]:
    total_stmt = select(func.count()).select_from(Item)
    total = (await session.execute(total_stmt)).scalar_one()

    items_stmt = select(Item).order_by(Item.id).limit(limit).offset(offset)
    items = (await session.execute(items_stmt)).scalars().all()
    return list(items), int(total)


async def update_item(session: AsyncSession, item: Item, payload: ItemUpdate) -> Item:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(item, field, value)
    await session.commit()
    await session.refresh(item)
    return item


async def delete_item(session: AsyncSession, item_id: int) -> int:
    result = await session.execute(delete(Item).where(Item.id == item_id))
    await session.commit()
    return result.rowcount or 0
