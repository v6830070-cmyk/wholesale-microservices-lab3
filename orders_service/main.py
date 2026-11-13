from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import models.orders  # noqa: F401  (регистрирует таблицы)
from database.connection import conn
from docs_ru import setup_docs_ru
from routes.orders import router as orders_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn()
    yield


app = FastAPI(title="Сервис заказов", version="2.0.0", lifespan=lifespan, docs_url=None)
setup_docs_ru(app)
app.include_router(orders_router)


@app.get("/health", include_in_schema=False)
def health():
    return {"service": "orders", "status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8002, reload=True)
