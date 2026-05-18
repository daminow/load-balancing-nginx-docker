from sqlalchemy import CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("char_length(name) > 0", name="ck_items_name_nonempty"),
        CheckConstraint("quantity >= 0", name="ck_items_quantity_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(length=120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
