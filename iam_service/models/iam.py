from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    password_hash: str = Field(max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=150)
    company_name: str = Field(max_length=255)
    inn: str = Field(max_length=12)
    role: str = Field(default="client", max_length=20)      # client / manager / admin
    status: str = Field(default="active", max_length=20)    # active / blocked
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Схемы запросов и ответов (не таблицы)
class RegisterIn(SQLModel):
    email: str
    password: str
    company_name: str
    inn: str
    full_name: Optional[str] = None


class LoginIn(SQLModel):
    email: str
    password: str


class TokenOut(SQLModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class UserOut(SQLModel):
    id: int
    email: str
    full_name: Optional[str] = None
    company_name: str
    inn: str
    role: str
    status: str
    created_at: datetime


class RoleUpdate(SQLModel):
    role: str


class StatusUpdate(SQLModel):
    status: str