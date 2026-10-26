from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn
from database.connection import conn
from routes.products import product_router
from routes.references import unit_router, category_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn()
    yield


app = FastAPI(
    title="Catalog service",
    description="Лабораторная работа №2. Автор: Игнатченко Владислав",
    contact={"name": "Игнатченко Владислав", "email": "vlad@example.com"},
    lifespan=lifespan,
)

app.include_router(unit_router, prefix="/unit")
app.include_router(category_router, prefix="/category")
app.include_router(product_router, prefix="/product")


@app.get("/")
async def home():
    return RedirectResponse(url="/product/")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)