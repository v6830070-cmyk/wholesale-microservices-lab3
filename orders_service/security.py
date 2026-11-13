"""Проверка JWT и ролей. Один и тот же файл используется во всех новых сервисах."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALGORITHM = "HS256"

# Ищем значение секрета по умолчанию в уже существующих сервисах (iam_service / catalog_service),
# чтобы токены, выданные IAM, принимались и новыми сервисами без ручной настройки.
_SECRET_RE = re.compile(
    r"""(?:getenv|environ\.get)\(\s*["'][A-Za-z_]*SECRET[A-Za-z_]*["']\s*,\s*["']([^"']+)["']"""
)


def _load_secret() -> str:
    value = os.getenv("JWT_SECRET")
    if value:
        return value
    root = Path(__file__).resolve().parent.parent
    for folder in ("iam_service", "catalog_service"):
        candidate = root / folder / "security.py"
        if candidate.exists():
            match = _SECRET_RE.search(candidate.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
    return "dev-secret-change-me"


SECRET_KEY = _load_secret()

ALL_ROLES = ("client", "manager", "admin", "storekeeper", "accountant", "director")

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    role: str
    email: Optional[str] = None
    token: str = ""
    roles: set = field(default_factory=set)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен просрочен")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")


def _extract_user_id(payload: dict) -> int:
    for key in ("user_id", "uid", "id", "sub"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "В токене нет идентификатора пользователя")


def _extract_roles(payload: dict) -> set:
    roles: set = set()
    single = payload.get("role")
    if isinstance(single, str):
        roles.add(single)
    many = payload.get("roles")
    if isinstance(many, str):
        roles.add(many)
    elif isinstance(many, (list, tuple, set)):
        roles.update(str(item) for item in many)
    return roles


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация")
    payload = decode_token(credentials.credentials)
    roles = _extract_roles(payload)
    if not roles:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "В токене нет роли")
    email = payload.get("email")
    if email is None and isinstance(payload.get("sub"), str) and "@" in payload["sub"]:
        email = payload["sub"]
    role_claim = payload.get("role")
    primary: str = role_claim if isinstance(role_claim, str) else sorted(roles)[0]
    return CurrentUser(
        id=_extract_user_id(payload),
        role=primary,
        email=email,
        token=credentials.credentials,
        roles=roles,
    )


def require_roles(*allowed: str):
    """Зависимость: пропускает только перечисленные роли (admin допускается всегда)."""

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "admin" in user.roles or user.roles.intersection(allowed):
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для этой операции")

    return checker


def has_role(user: CurrentUser, *names: str) -> bool:
    return "admin" in user.roles or bool(user.roles.intersection(names))
