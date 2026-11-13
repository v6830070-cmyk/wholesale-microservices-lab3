from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(index=True)
    number: Optional[str] = Field(default=None, index=True)
    status: str = "pending"
    order_date: datetime = Field(default_factory=utcnow)
    delivery_address: Optional[str] = None
    total_amount: float = 0.0
    currency: str = "RUB"
    comment: Optional[str] = None


class OrderItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id", index=True)
    product_id: int
    qty: int
    price: float
    discount: float = 0.0
    total: float


class OrderStatusHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id", index=True)
    status: str
    changed_by: Optional[int] = None
    changed_at: datetime = Field(default_factory=utcnow)
