"""API Gateway: единая точка входа, проверка JWT, ограничение частоты, маршрутизация по префиксу."""
import asyncio
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import httpx
import jwt
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from security import ALGORITHM, SECRET_KEY

SERVICES = {
    "iam": os.getenv("IAM_URL", "http://127.0.0.1:8003"),
    "catalog": os.getenv("CATALOG_URL", "http://127.0.0.1:8001"),
    "orders": os.getenv("ORDERS_URL", "http://127.0.0.1:8002"),
    "clients": os.getenv("CLIENTS_URL", "http://127.0.0.1:8004"),
    "warehouse": os.getenv("WAREHOUSE_URL", "http://127.0.0.1:8005"),
    "finance": os.getenv("FINANCE_URL", "http://127.0.0.1:8006"),
    "reporting": os.getenv("REPORTING_URL", "http://127.0.0.1:8007"),
}

# Публичные маршруты IAM: регистрация и вход (окончание пути).
PUBLIC_SUFFIXES = ("register", "login")

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "120"))
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))
_hits: "defaultdict[str, deque]" = defaultdict(deque)

HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    yield
    await app.state.http.aclose()


app = FastAPI(title="Шлюз API", version="2.0.0", lifespan=lifespan)


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return True
    bucket.append(now)
    return False


@app.get("/health")
async def health(request: Request):
    client: httpx.AsyncClient = request.app.state.http

    async def probe(name: str, base: str):
        try:
            response = await client.get(f"{base}/openapi.json", timeout=3.0)
            return name, "up" if response.status_code == 200 else f"http {response.status_code}"
        except httpx.HTTPError:
            return name, "down"

    results = await asyncio.gather(*(probe(name, base) for name, base in SERVICES.items()))
    return {"gateway": "ok", "services": dict(results)}


@app.api_route("/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, path: str, request: Request):
    base = SERVICES.get(service)
    if base is None:
        return JSONResponse({"detail": f"Неизвестный сервис «{service}»"}, status_code=404)

    is_public = service == "iam" and path.rstrip("/").endswith(PUBLIC_SUFFIXES)
    rate_key = request.client.host if request.client else "unknown"

    if not is_public:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"detail": "Требуется авторизация"}, status_code=401)
        token = header[7:].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            return JSONResponse({"detail": "Токен просрочен"}, status_code=401)
        except jwt.InvalidTokenError:
            return JSONResponse({"detail": "Недействительный токен"}, status_code=401)
        rate_key = f"user:{payload.get('sub') or payload.get('user_id') or rate_key}"

    if _rate_limited(rate_key):
        return JSONResponse({"detail": "Слишком много запросов"}, status_code=429)

    url = f"{base}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    body = await request.body()
    client: httpx.AsyncClient = request.app.state.http
    try:
        upstream = await client.request(
            request.method, url, params=request.query_params, headers=headers, content=body
        )
    except httpx.HTTPError:
        return JSONResponse({"detail": f"Сервис «{service}» недоступен"}, status_code=503)

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() in ("content-type", "location", "www-authenticate")
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.getenv("GATEWAY_PORT", "8000")), reload=True)
