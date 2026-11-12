import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn
from sqlmodel import Session, select

from database.connection import conn, engine_url
from models.iam import User
from routes.auth import auth_router
from routes.users import users_router
from security import hash_password

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin12345")


def seed_admin():
    """Создаёт администратора при первом запуске."""
    with Session(engine_url) as session:
        exists = session.exec(select(User).where(User.email == ADMIN_EMAIL)).first()
        if not exists:
            session.add(User(email=ADMIN_EMAIL,
                             password_hash=hash_password(ADMIN_PASSWORD),
                             full_name="Администратор",
                             company_name="Оптовая фирма",
                             inn="0000000000",
                             role="admin"))
            session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn()
    seed_admin()
    yield


app = FastAPI(
    title="IAM service",
    description="Лабораторная работа №3. Автор: Игнатченко Владислав",
    contact={"name": "Игнатченко Владислав", "email": "vlad@example.com"},
    lifespan=lifespan,
)

app.include_router(auth_router, prefix="/auth")
app.include_router(users_router, prefix="/users")


@app.get("/")
async def home():
    return RedirectResponse(url="/docs")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)