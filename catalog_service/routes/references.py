from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.catalog import Unit, Category
from security import get_current_user, require_roles

unit_router = APIRouter(tags=["Units"])
category_router = APIRouter(tags=["Categories"])


@unit_router.post("/new", status_code=status.HTTP_201_CREATED,
                  dependencies=[Depends(require_roles("manager", "admin"))])
async def create_unit(new_unit: Unit, session: Session = Depends(get_session)) -> dict:
    new_unit.id = None  # id назначает база данных
    if session.exec(select(Unit).where(Unit.code == new_unit.code)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Unit with this code already exists")
    session.add(new_unit)
    session.commit()
    session.refresh(new_unit)
    return {"message": "Unit created successfully", "id": new_unit.id}


@unit_router.get("/", response_model=List[Unit],
                 dependencies=[Depends(get_current_user)])
async def retrieve_units(session: Session = Depends(get_session)):
    return session.exec(select(Unit)).all()


@category_router.post("/new", status_code=status.HTTP_201_CREATED,
                      dependencies=[Depends(require_roles("manager", "admin"))])
async def create_category(new_category: Category,
                          session: Session = Depends(get_session)) -> dict:
    new_category.id = None  # id назначает база данных
    if session.exec(select(Category).where(Category.slug == new_category.slug)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Category with this slug already exists")
    session.add(new_category)
    session.commit()
    session.refresh(new_category)
    return {"message": "Category created successfully", "id": new_category.id}


@category_router.get("/", response_model=List[Category],
                     dependencies=[Depends(get_current_user)])
async def retrieve_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category)).all()