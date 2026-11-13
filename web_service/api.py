"""Вызовы API через gateway и разбор схемы IAM (openapi.json) для форм входа и регистрации."""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import jwt

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
IAM_URL = os.getenv("IAM_URL", "http://127.0.0.1:8003")
CATALOG_URL = os.getenv("CATALOG_URL", "http://127.0.0.1:8001")

ROLES = ["client", "manager", "admin", "storekeeper", "accountant", "director"]


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        return response.text[:300] or f"HTTP {response.status_code}"
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        return "; ".join(f"{key}: {value}" for key, value in detail.items())
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                where = ".".join(str(x) for x in item.get("loc", [])[1:])
                parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(detail)


def call(method: str, path: str, token: Optional[str] = None, *, json: Any = None,
         data: Any = None, params: Optional[dict] = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    try:
        response = httpx.request(
            method, GATEWAY_URL + path, headers=headers, json=json, data=data, params=clean, timeout=30.0
        )
    except httpx.HTTPError:
        raise ApiError(503, "Шлюз недоступен: проверьте, что gateway (порт 8000) запущен")
    if response.status_code >= 400:
        raise ApiError(response.status_code, _detail(response))
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


# ---------- токен и пользователь ----------

def user_from_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        return None
    uid = None
    for key in ("user_id", "uid", "id", "sub"):
        if key in payload:
            try:
                uid = int(payload[key])
                break
            except (TypeError, ValueError):
                continue
    roles = set()
    if isinstance(payload.get("role"), str):
        roles.add(payload["role"])
    many = payload.get("roles")
    if isinstance(many, str):
        roles.add(many)
    elif isinstance(many, (list, tuple, set)):
        roles.update(str(x) for x in many)
    if uid is None or not roles:
        return None
    email = payload.get("email")
    if email is None and isinstance(payload.get("sub"), str) and "@" in payload["sub"]:
        email = payload["sub"]
    primary = payload["role"] if isinstance(payload.get("role"), str) else sorted(roles)[0]
    return {"id": uid, "email": email, "roles": roles, "role": primary, "token": token}


def has(user: Optional[dict], *names: str) -> bool:
    if not user:
        return False
    return "admin" in user["roles"] or bool(user["roles"].intersection(names))


# ---------- схема IAM ----------

_spec: Optional[dict] = None


def _load_spec() -> dict:
    global _spec
    if _spec is None:
        try:
            _spec = httpx.get(f"{IAM_URL}/openapi.json", timeout=10.0).json()
        except httpx.HTTPError:
            raise ApiError(503, "Сервис IAM (порт 8003) недоступен")
    return _spec


def _resolve(schema: dict) -> dict:
    if "$ref" in schema:
        return _load_spec()["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def _find(word: str, method: str, no_params: bool = False) -> Optional[Tuple[str, dict]]:
    for path, operations in _load_spec()["paths"].items():
        if word in path.lower() and method in operations and (not no_params or "{" not in path):
            return path, operations[method]
    return None


def _body(operation: dict) -> Tuple[Optional[str], Dict[str, dict], set]:
    content = operation.get("requestBody", {}).get("content", {})
    for kind in ("application/json", "application/x-www-form-urlencoded"):
        if kind in content:
            schema = _resolve(content[kind]["schema"])
            return kind, schema.get("properties", {}), set(schema.get("required", []))
    return None, {}, set()


def _send(method: str, prefix: str, path: str, operation: dict, payload: dict, token: Optional[str]) -> Any:
    kind, _, _ = _body(operation)
    if kind == "application/x-www-form-urlencoded":
        return call(method, prefix + path, token, data=payload)
    return call(method, prefix + path, token, json=payload)


def register_fields() -> List[dict]:
    found = _find("register", "post")
    if not found:
        raise ApiError(500, "В IAM не найден маршрут регистрации")
    _, props, required = _body(found[1])
    fields = []
    for name, definition in props.items():
        fields.append({"name": name, "required": name in required, "type": definition.get("type", "string")})
    return fields


def register(values: Dict[str, str]) -> None:
    path, operation = _find("register", "post")  # type: ignore[misc]
    _, props, _ = _body(operation)
    payload = {name: values[name] for name in props if values.get(name, "") != ""}
    _send("POST", "/iam", path, operation, payload, None)


def login(email: str, password: str) -> str:
    path, operation = _find("login", "post")  # type: ignore[misc]
    _, props, _ = _body(operation)
    payload: Dict[str, Any] = {}
    for name in props:
        if name in ("email", "username"):
            payload[name] = email
        elif name == "password":
            payload[name] = password
    data = _send("POST", "/iam", path, operation, payload, None)
    token = (data or {}).get("access_token") or (data or {}).get("token")
    if not token:
        raise ApiError(500, "IAM не вернул токен")
    return token


def list_users(token: str) -> List[dict]:
    found = _find("users", "get", no_params=True)
    if not found:
        raise ApiError(404, "В IAM не найден маршрут со списком пользователей")
    return call("GET", "/iam" + (found[0] if found[0].endswith("/") else found[0] + "/"), token)


def set_role(token: str, user_id: int, role: str) -> None:
    found = _find("role", "put")
    if not found:
        raise ApiError(404, "В IAM не найден маршрут смены роли")
    path, operation = found
    for part in ("{user_id}", "{id}", "{uid}"):
        path = path.replace(part, str(user_id))
    _, props, _ = _body(operation)
    if props:
        payload = {"role": role} if "role" in props else {next(iter(props)): role}
        _send("PUT", "/iam", path, operation, payload, token)
    else:
        call("PUT", "/iam" + path, token, params={"role": role})


# ---------- каталог ----------

_product_path: Optional[str] = None


def product_list_path() -> str:
    global _product_path
    if _product_path is None:
        try:
            spec = httpx.get(f"{CATALOG_URL}/openapi.json", timeout=10.0).json()
        except httpx.HTTPError:
            raise ApiError(503, "Сервис каталога (порт 8001) недоступен")
        for path, operations in spec["paths"].items():
            if "product" in path.lower() and "{" not in path and "get" in operations:
                _product_path = path if path.endswith("/") else path + "/"
                break
        if _product_path is None:
            raise ApiError(500, "В каталоге не найден маршрут списка товаров")
    return _product_path


def products(token: str) -> List[dict]:
    data = call("GET", "/catalog" + product_list_path(), token)
    return data if isinstance(data, list) else data.get("items", [])
