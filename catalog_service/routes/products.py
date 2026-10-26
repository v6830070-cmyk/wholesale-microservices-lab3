import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from database.connection import get_session
from models.catalog import Product, ProductUpdate

product_router = APIRouter(tags=["Products"])


@product_router.post("/new", status_code=status.HTTP_201_CREATED)
async def create_product(new_product: Product,
                         session: Session = Depends(get_session)) -> dict:
    if session.exec(select(Product).where(Product.sku == new_product.sku)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Product with this SKU already exists")
    session.add(new_product)
    session.commit()
    session.refresh(new_product)
    return {"message": "Product created successfully", "id": str(new_product.id)}


@product_router.get("/", response_model=List[Product])
async def retrieve_all_products(category_id: Optional[uuid.UUID] = None,
                                session: Session = Depends(get_session)):
    statement = select(Product)
    if category_id:
        statement = statement.where(Product.category_id == category_id)
    return session.exec(statement).all()


@product_router.get("/{id}", response_model=Product)
async def retrieve_product(id: uuid.UUID, session: Session = Depends(get_session)):
    product = session.get(Product, id)
    if product:
        return product
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Product with supplied ID does not exist")


@product_router.put("/edit/{id}", response_model=Product)
async def update_product(id: uuid.UUID, new_data: ProductUpdate,
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
                        detail="Product with supplied ID does not exist")


@product_router.delete("/delete/{id}")
async def delete_product(id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    product = session.get(Product, id)
    if product:
        session.delete(product)
        session.commit()
        return {"message": "Product deleted successfully"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Product with supplied ID does not exist")