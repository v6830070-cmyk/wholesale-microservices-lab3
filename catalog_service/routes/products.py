from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.catalog import Product, ProductUpdate
from security import get_current_user, require_roles

product_router = APIRouter(tags=["Товары"])

manager_or_admin = Depends(require_roles("manager", "admin"))


@product_router.post("/new", status_code=status.HTTP_201_CREATED,
                     dependencies=[manager_or_admin], summary="Создать товар")
async def create_product(new_product: Product,
                         session: Session = Depends(get_session)) -> dict:
    new_product.id = None  # id назначает база данных
    if session.exec(select(Product).where(Product.sku == new_product.sku)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Товар с таким артикулом уже существует")
    session.add(new_product)
    session.commit()
    session.refresh(new_product)
    return {"message": "Товар успешно создан", "id": new_product.id}


@product_router.get("/", response_model=List[Product],
                    dependencies=[Depends(get_current_user)],
                    summary="Список товаров")
async def retrieve_all_products(category_id: Optional[int] = None,
                                session: Session = Depends(get_session)):
    statement = select(Product)
    if category_id:
        statement = statement.where(Product.category_id == category_id)
    return session.exec(statement).all()


@product_router.get("/{id}", response_model=Product,
                    dependencies=[Depends(get_current_user)],
                    summary="Товар по ID")
async def retrieve_product(id: int, session: Session = Depends(get_session)):
    product = session.get(Product, id)
    if product:
        return product
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Товар с указанным ID не найден")


@product_router.put("/edit/{id}", response_model=Product,
                    dependencies=[manager_or_admin], summary="Изменить товар")
async def update_product(id: int, new_data: ProductUpdate,
                         session: Session = Depends(get_session)):
    product = session.get(Product, id)
    if product:
        product_data = new_data.model_dump(exclude_unset=True)
        for key, value in product_data.items():
            setattr(product, key, value)
        session.add(product)
        session.commit()
        session.refresh(product)
        return product
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Товар с указанным ID не найден")


@product_router.delete("/delete/{id}", dependencies=[manager_or_admin],
                       summary="Удалить товар")
async def delete_product(id: int, session: Session = Depends(get_session)) -> dict:
    product = session.get(Product, id)
    if product:
        session.delete(product)
        session.commit()
        return {"message": "Товар успешно удалён"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Товар с указанным ID не найден")
