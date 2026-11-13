from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.catalog import Unit, Category
from security import get_current_user, require_roles

unit_router = APIRouter(tags=["Единицы измерения"])
category_router = APIRouter(tags=["Категории"])


@unit_router.post("/new", status_code=status.HTTP_201_CREATED,
                  dependencies=[Depends(require_roles("manager", "admin"))],
                  summary="Создать единицу измерения")
async def create_unit(new_unit: Unit, session: Session = Depends(get_session)) -> dict:
    new_unit.id = None  # id назначает база данных
    if session.exec(select(Unit).where(Unit.code == new_unit.code)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Единица измерения с таким кодом уже существует")
    session.add(new_unit)
    session.commit()
    session.refresh(new_unit)
    return {"message": "Единица измерения успешно создана", "id": new_unit.id}


@unit_router.get("/", response_model=List[Unit],
                 dependencies=[Depends(get_current_user)],
                 summary="Список единиц измерения")
async def retrieve_units(session: Session = Depends(get_session)):
    return session.exec(select(Unit)).all()


@category_router.post("/new", status_code=status.HTTP_201_CREATED,
                      dependencies=[Depends(require_roles("manager", "admin"))],
                      summary="Создать категорию")
async def create_category(new_category: Category,
                          session: Session = Depends(get_session)) -> dict:
    new_category.id = None  # id назначает база данных
    if session.exec(select(Category).where(Category.slug == new_category.slug)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Категория с таким slug уже существует")
    session.add(new_category)
    session.commit()
    session.refresh(new_category)
    return {"message": "Категория успешно создана", "id": new_category.id}


@category_router.get("/", response_model=List[Category],
                     dependencies=[Depends(get_current_user)],
                     summary="Список категорий")
async def retrieve_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category)).all()
