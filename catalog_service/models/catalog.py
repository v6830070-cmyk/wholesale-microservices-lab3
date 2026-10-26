from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Unit(SQLModel, table=True):
    __tablename__ = "units"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, max_length=10)
    name: str = Field(max_length=50)


class Category(SQLModel, table=True):
    __tablename__ = "categories"
    id: Optional[int] = Field(default=None, primary_key=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="categories.id")
    name: str = Field(max_length=150)
    slug: str = Field(unique=True, max_length=150)


class Product(SQLModel, table=True):
    __tablename__ = "products"
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(unique=True, index=True, max_length=64)
    name: str = Field(max_length=255)
    category_id: int = Field(foreign_key="categories.id")
    unit_id: int = Field(foreign_key="units.id")
    description: Optional[str] = None
    weight_kg: float = 0
    cost_price: float = 0
    base_price: float = 0
    min_qty: int = 1
    status: str = "active"  # active / discontinued
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Схема для обновления (не таблица): все поля необязательные
class ProductUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    weight_kg: Optional[float] = None
    cost_price: Optional[float] = None
    base_price: Optional[float] = None
    min_qty: Optional[int] = None
    status: Optional[str] = None