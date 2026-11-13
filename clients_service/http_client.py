"""Синхронные REST-вызовы между сервисами (токен пользователя пробрасывается дальше)."""
import os
from typing import Any, Optional

import httpx
from fastapi import HTTPException

TIMEOUT = float(os.getenv("SERVICE_TIMEOUT", "10"))

CATALOG_URL = os.getenv("CATALOG_URL", "http://127.0.0.1:8001")
ORDERS_URL = os.getenv("ORDERS_URL", "http://127.0.0.1:8002")
IAM_URL = os.getenv("IAM_URL", "http://127.0.0.1:8003")
CLIENTS_URL = os.getenv("CLIENTS_URL", "http://127.0.0.1:8004")
WAREHOUSE_URL = os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8005")
FINANCE_URL = os.getenv("FINANCE_URL", "http://127.0.0.1:8006")
REPORTING_URL = os.getenv("REPORTING_URL", "http://127.0.0.1:8007")

# В каталоге карточка товара может отдаваться по /product/{id} или /products/{id}.
PRODUCT_PATHS = ("/products/{id}", "/product/{id}")
_working_product_path: Optional[str] = None


def call(
    method: str,
    url: str,
    token: str,
    *,
    json: Any = None,
    params: Optional[dict] = None,
    service: str = "сервис",
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            return client.request(method, url, headers=headers, json=json, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Сервис «{service}» недоступен ({exc.__class__.__name__})")


def get_product(token: str, product_id: int) -> Optional[dict]:
    """Возвращает карточку товара из каталога или None, если товара нет."""
    global _working_product_path
    paths = [_working_product_path] if _working_product_path else list(PRODUCT_PATHS)
    for template in paths:
        response = call(
            "GET",
            CATALOG_URL + template.format(id=product_id),
            token,
            service="catalog",
        )
        if response.status_code == 200:
            _working_product_path = template
            return response.json()
        if response.status_code == 404:
            continue
        if response.status_code in (401, 403):
            raise HTTPException(
                502,
                f"Каталог отказал в доступе к товару (статус {response.status_code}); "
                "проверьте роли в catalog_service",
            )
        raise HTTPException(502, f"Каталог вернул неожиданный статус {response.status_code}")
    return None
