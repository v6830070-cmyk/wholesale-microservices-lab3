"""Чистая бизнес-логика сервиса клиентов (без зависимостей от FastAPI и БД)."""
from typing import Optional

CLIENT_STATUSES = ("new", "active", "blocked")


def validate_inn(inn: str) -> str:
    value = (inn or "").strip()
    if not value.isdigit() or len(value) not in (10, 12):
        raise ValueError("ИНН должен состоять из 10 или 12 цифр")
    return value


def validate_kpp(kpp: Optional[str]) -> Optional[str]:
    if kpp is None or kpp == "":
        return None
    value = kpp.strip()
    if not value.isdigit() or len(value) != 9:
        raise ValueError("КПП должен состоять из 9 цифр")
    return value


def validate_credit_limit(limit: float) -> float:
    if limit < 0:
        raise ValueError("Кредитный лимит не может быть отрицательным")
    return round(float(limit), 2)
