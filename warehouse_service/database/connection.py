import os

from sqlmodel import Session, SQLModel, create_engine

DB_URL = os.getenv("WAREHOUSE_DB_URL", "sqlite:///./warehouse.db")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


def conn() -> None:
    """Создаёт таблицы (модели должны быть импортированы до вызова)."""
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
