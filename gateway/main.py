import os
import time
from collections import defaultdict, deque

import httpx
import jwt
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

JWT_SECRET = os.getenv("JWT_SECRET", "wholesale-lab3-dev-secret-key-change-me-0123456789")
JWT_ALGORITHM = "HS256"

# Адреса сервисов: первая часть пути запроса выбирает сервис
SERVICES = {
    "iam": "http://127.0.0.1:8003",
    "catalog": "http://127.0.0.1:8001",
    "orders": "http://127.0.0.1:8002",
}

# Маршруты, доступные без токена: (метод, путь без начального и конечного "/")
PUBLIC_ROUTES = {
    ("POST", "iam/auth/register"),
    ("POST", "iam/auth/login"),
}

# Ограничение нагрузки: не более RATE_LIMIT запросов за RATE_WINDOW секунд с одного IP
RATE_LIMIT = 120
RATE_WINDOW = 60
hits = defaultdict(deque)

# Заголовки, которые не пересылаются на сервис
SKIPPED_HEADERS = {"host", "content-length", "connection", "keep-alive",
                   "transfer-encoding", "accept-encoding"}

app = FastAPI(
    title="API Gateway",
    description="Лабораторная работа №3. Автор: Игнатченко Владислав",
    contact={"name": "Игнатченко Владислав", "email": "vlad@example.com"},
)


def is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    queue = hits[ip]
    while queue and now - queue[0] > RATE_WINDOW:
        queue.popleft()
    if len(queue) >= RATE_LIMIT:
        return True
    queue.append(now)
    return False


def unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401,
                        headers={"WWW-Authenticate": "Bearer"})


@app.get("/")
async def home() -> dict:
    return {"service": "API Gateway",
            "routes": {name: f"/{name}/..." for name in SERVICES},
            "health": "/health"}


@app.get("/health")
async def health() -> dict:
    result = {}
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, base in SERVICES.items():
            try:
                resp = await client.get(f"{base}/openapi.json")
                result[name] = "up" if resp.status_code == 200 else f"error {resp.status_code}"
            except httpx.RequestError:
                result[name] = "down"
    return result


@app.api_route("/{service}/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
               include_in_schema=False)
async def proxy(service: str, path: str, request: Request):
    ip = request.client.host if request.client else "unknown"
    if is_rate_limited(ip):
        return JSONResponse({"detail": "Too many requests"}, status_code=429)

    base = SERVICES.get(service)
    if base is None:
        return JSONResponse({"detail": f"Unknown service '{service}'"}, status_code=404)

    route_key = (request.method, f"{service}/{path}".rstrip("/"))
    if route_key not in PUBLIC_ROUTES:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return unauthorized("Not authenticated")
        try:
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return unauthorized("Token expired")
        except jwt.PyJWTError:
            return unauthorized("Invalid token")

    url = f"{base}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {key: value for key, value in request.headers.items()
               if key.lower() not in SKIPPED_HEADERS}
    headers["X-Forwarded-For"] = ip
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.request(request.method, url,
                                            content=body, headers=headers)
    except httpx.RequestError:
        return JSONResponse({"detail": f"Service '{service}' is unavailable"},
                            status_code=503)

    return Response(content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)