from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True, unique=True)
    inn: str = Field(index=True, unique=True)
    kpp: Optional[str] = None
    name: str
    legal_address: str
    status: str = "new"
    manager_id: Optional[int] = None
    credit_limit: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    full_name: str
    position: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
