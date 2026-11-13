from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import models.clients  # noqa: F401  (регистрирует таблицы)
from database.connection import conn
from docs_ru import setup_docs_ru
from routes.clients import router as clients_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn()
    yield


app = FastAPI(title="Сервис клиентов", version="1.0.0", lifespan=lifespan, docs_url=None)
setup_docs_ru(app)
app.include_router(clients_router)


@app.get("/health", include_in_schema=False)
def health():
    return {"service": "clients", "status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8004, reload=True)
