from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn
from database.connection import conn
from routes.orders import order_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn()
    yield


app = FastAPI(
    title="Orders service",
    description="Лабораторная работа №2. Автор: Игнатченко Владислав",
    contact={"name": "Игнатченко Владислав", "email": "vlad@example.com"},
    lifespan=lifespan,
)

app.include_router(order_router, prefix="/order")


@app.get("/")
async def home():
    return RedirectResponse(url="/order/")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)