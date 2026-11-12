from datetime import datetime, timezone
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.orders import (Order, OrderItem, OrderStatusHistory,
                           OrderCreate, OrderUpdate)
from security import CurrentUser, get_current_user, require_roles

order_router = APIRouter(tags=["Orders"])

CATALOG_URL = "http://127.0.0.1:8001"
ALLOWED_STATUSES = {"pending", "confirmed", "shipped", "cancelled"}
CANCELLABLE_STATUSES = {"pending", "confirmed"}


def check_access(order: Order, user: CurrentUser) -> None:
    """Клиент имеет доступ только к своим заказам."""
    if user.role == "client" and order.client_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied")


@order_router.post("/new", status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate,
                       user: CurrentUser = Depends(get_current_user),
                       session: Session = Depends(get_session)) -> dict:
    if not data.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Order must contain at least one item")
    # Определяем клиента: для роли client это сам пользователь
    if user.role == "client":
        client_id = user.id
    else:
        if data.client_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="client_id is required for manager and admin")
        client_id = data.client_id

    # 1. Запрашиваем товары у сервиса catalog, передавая тот же токен
    lines = []
    auth_headers = {"Authorization": f"Bearer {user.token}"}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for item in data.items:
            try:
                resp = await client.get(f"{CATALOG_URL}/product/{item.product_id}",
                                        headers=auth_headers)
            except httpx.RequestError:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="Catalog service is unavailable")
            if resp.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                    detail=f"Product {item.product_id} not found")
            if resp.status_code == 401:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                    detail="Catalog service rejected the token")
            if resp.status_code != 200:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                    detail="Unexpected response from catalog service")
            product = resp.json()
            if product["status"] != "active":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Product {product['sku']} is discontinued")
            if item.qty < product["min_qty"]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Min qty for {product['sku']} is {product['min_qty']}")
            lines.append((item, product["base_price"]))

    # 2. Создаём заказ и позиции в своей БД
    order = Order(client_id=client_id,
                  number=f"ORD-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}",
                  delivery_address=data.delivery_address,
                  comment=data.comment)
    session.add(order)
    session.flush()  # получаем order.id от базы данных
    total = 0.0
    for item, price in lines:
        line_total = round(price * item.qty, 2)
        total += line_total
        session.add(OrderItem(order_id=order.id, product_id=item.product_id,
                              qty=item.qty, price=price, total=line_total))
    order.total_amount = round(total, 2)
    session.add(OrderStatusHistory(order_id=order.id, status=order.status,
                                   changed_by=user.id))
    session.commit()
    session.refresh(order)
    return {"message": "Order created successfully",
            "id": order.id, "number": order.number,
            "total_amount": order.total_amount}


@order_router.get("/", response_model=List[Order])
async def retrieve_all_orders(user: CurrentUser = Depends(get_current_user),
                              session: Session = Depends(get_session)):
    statement = select(Order)
    if user.role == "client":
        statement = statement.where(Order.client_id == user.id)
    return session.exec(statement).all()


@order_router.get("/{id}")
async def retrieve_order(id: int,
                         user: CurrentUser = Depends(get_current_user),
                         session: Session = Depends(get_session)) -> dict:
    order = session.get(Order, id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Order with supplied ID does not exist")
    check_access(order, user)
    items = session.exec(select(OrderItem).where(OrderItem.order_id == id)).all()
    history = session.exec(select(OrderStatusHistory)
                           .where(OrderStatusHistory.order_id == id)).all()
    return {"order": order, "items": items, "history": history}


@order_router.put("/edit/{id}", response_model=Order)
async def update_order(id: int, new_data: OrderUpdate,
                       user: CurrentUser = Depends(require_roles("manager", "admin")),
                       session: Session = Depends(get_session)):
    order = session.get(Order, id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Order with supplied ID does not exist")
    if order.status == "shipped":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Order already shipped, change is impossible")
    order_data = new_data.model_dump(exclude_unset=True)
    if "status" in order_data and order_data["status"] not in ALLOWED_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Status must be one of {sorted(ALLOWED_STATUSES)}")
    for key, value in order_data.items():
        setattr(order, key, value)
    if "status" in order_data:
        session.add(OrderStatusHistory(order_id=order.id, status=order.status,
                                       changed_by=user.id))
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


@order_router.put("/cancel/{id}", response_model=Order)
async def cancel_order(id: int,
                       user: CurrentUser = Depends(get_current_user),
                       session: Session = Depends(get_session)):
    order = session.get(Order, id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Order with supplied ID does not exist")
    check_access(order, user)
    if order.status not in CANCELLABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Order with status {order.status} cannot be cancelled")
    order.status = "cancelled"
    session.add(OrderStatusHistory(order_id=order.id, status=order.status,
                                   changed_by=user.id))
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


@order_router.delete("/delete/{id}",
                     dependencies=[Depends(require_roles("admin"))])
async def delete_order(id: int, session: Session = Depends(get_session)) -> dict:
    order = session.get(Order, id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Order with supplied ID does not exist")
    for model in (OrderItem, OrderStatusHistory):
        for row in session.exec(select(model).where(model.order_id == id)).all():
            session.delete(row)
    session.delete(order)
    session.commit()
    return {"message": "Order deleted successfully"}