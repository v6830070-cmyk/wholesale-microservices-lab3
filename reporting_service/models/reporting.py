from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetricSale(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("order_id", "product_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(index=True)
    product_id: int = Field(index=True)
    client_id: int = Field(index=True)
    order_date: date
    month: str = Field(index=True)  # YYYY-MM
    qty: int
    revenue: float
    cost: float
    margin: float
    exported_at: datetime = Field(default_factory=utcnow)


class Report(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    parameters: str  # JSON: date_from, date_to, group_by
    created_by: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
