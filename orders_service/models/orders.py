from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import SQLModel, Field


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int                            # ссылка на пользователя IAM (без FK)
    number: str = Field(unique=True, max_length=30)
    status: str = "pending"                   # pending / confirmed / shipped / cancelled
    order_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivery_address: str = Field(max_length=500)
    total_amount: float = 0
    currency: str = Field(default="RUB", max_length=3)
    comment: Optional[str] = None


class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id")
    product_id: int                           # ссылка на catalog.products (без FK)
    qty: float
    price: float                              # цена на момент заказа
    discount: float = 0
    total: float


class OrderStatusHistory(SQLModel, table=True):
    __tablename__ = "order_status_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id")
    status: str
    changed_by: Optional[int] = None          # ссылка на пользователя (без FK)
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Схемы запросов (не таблицы)
class OrderItemIn(SQLModel):
    product_id: int
    qty: float


class OrderCreate(SQLModel):
    # Для клиента игнорируется (берётся из токена); для менеджера и администратора обязателен
    client_id: Optional[int] = None
    delivery_address: str
    comment: Optional[str] = None
    items: List[OrderItemIn]


class OrderUpdate(SQLModel):
    status: Optional[str] = None
    delivery_address: Optional[str] = None
    comment: Optional[str] = None