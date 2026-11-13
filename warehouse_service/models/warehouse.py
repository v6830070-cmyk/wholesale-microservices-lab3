from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Warehouse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    address: Optional[str] = None
    is_active: bool = True


class Stock(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("warehouse_id", "product_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    product_id: int = Field(index=True)
    qty: int = 0
    reserved_qty: int = 0
    min_qty: int = 0


class Reserve(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(index=True)
    product_id: int
    warehouse_id: int
    qty: int
    status: str = "active"  # active / released / shipped
    created_at: datetime = Field(default_factory=utcnow)


class Shipment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(index=True)
    number: Optional[str] = None
    status: str = "shipped"
    shipped_by: Optional[int] = None
    shipped_at: datetime = Field(default_factory=utcnow)


class ShipmentItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: int = Field(foreign_key="shipment.id", index=True)
    product_id: int
    warehouse_id: int
    qty: int


class StockMovement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(index=True)
    warehouse_id: int
    type: str  # receipt / shipment / writeoff / adjustment
    qty: int  # со знаком: приход > 0, расход < 0
    document_type: Optional[str] = None
    document_id: Optional[int] = None
    comment: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
