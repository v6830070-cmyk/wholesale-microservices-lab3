from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from database.connection import get_session
from http_client import CLIENTS_URL, WAREHOUSE_URL, call, get_product
from logic import EDITABLE_STATUSES, CREDIT_EXPOSURE_STATUSES, can_edit_status, exceeds_credit, line_total, merge_items
from models.orders import Order, OrderItem, OrderStatusHistory
from security import CurrentUser, get_current_user, has_role, require_roles

router = APIRouter(prefix="/order", tags=["Заказы"])

STAFF_READ = ("manager", "admin", "accountant", "storekeeper", "director")
INACTIVE_PRODUCT_STATUSES = ("inactive", "disabled", "archived", "discontinued", "blocked")


class OrderItemIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: int
    qty: int = Field(gt=0, validation_alias=AliasChoices("qty", "quantity"))


class OrderCreate(BaseModel):
    items: List[OrderItemIn] = Field(min_length=1)
    delivery_address: Optional[str] = None
    comment: Optional[str] = None
    currency: str = "RUB"
    client_id: Optional[int] = None  # только для менеджера/админа (заказ от имени клиента)


class OrderEdit(BaseModel):
    status: Optional[str] = None
    delivery_address: Optional[str] = None
    comment: Optional[str] = None


def _serialize(session: Session, order: Order) -> dict:
    items = session.exec(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    return {
        "id": order.id,
        "number": order.number,
        "client_id": order.client_id,
        "status": order.status,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "delivery_address": order.delivery_address,
        "total_amount": order.total_amount,
        "currency": order.currency,
        "comment": order.comment,
        "items": [
            {
                "product_id": item.product_id,
                "qty": item.qty,
                "price": item.price,
                "discount": item.discount,
                "total": item.total,
            }
            for item in items
        ],
    }


def _get_order(session: Session, order_id: int) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    return order


def _check_read_access(user: CurrentUser, order: Order) -> None:
    if has_role(user, *STAFF_READ) or order.client_id == user.id:
        return
    raise HTTPException(403, "Недостаточно прав")


def _history(session: Session, order_id: int, status: str, user: CurrentUser) -> None:
    session.add(OrderStatusHistory(order_id=order_id, status=status, changed_by=user.id))


def _release_reserve(order_id: int, user: CurrentUser) -> None:
    response = call("POST", f"{WAREHOUSE_URL}/warehouse/release/{order_id}", user.token, service="warehouse")
    if response.status_code not in (200, 201, 204):
        raise HTTPException(502, f"Не удалось снять резерв на складе (статус {response.status_code})")


def _load_client_card(client_id: int, user: CurrentUser) -> dict:
    response = call("GET", f"{CLIENTS_URL}/client/by-user/{client_id}", user.token, service="clients")
    if response.status_code == 404:
        raise HTTPException(409, "ClientCardRequired: у клиента нет карточки (создайте её в сервисе clients)")
    if response.status_code != 200:
        raise HTTPException(502, f"Сервис клиентов вернул статус {response.status_code}")
    return response.json()


# ---------- UC-05: оформление заказа ----------

@router.post("/new", status_code=201, summary="Оформить заказ")
def create_order(
    data: OrderCreate,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    client_id = user.id
    if data.client_id is not None and has_role(user, "manager"):
        client_id = data.client_id

    merged = merge_items((item.product_id, item.qty) for item in data.items)

    # 1. Цена и минимальная партия берутся из каталога.
    lines = []
    for product_id, qty in merged.items():
        product = get_product(user.token, product_id)
        if product is None:
            raise HTTPException(404, f"Товар {product_id} не найден в каталоге")
        if str(product.get("status", "")).lower() in INACTIVE_PRODUCT_STATUSES:
            raise HTTPException(409, f"Товар {product_id} снят с продажи")
        min_qty = int(product.get("min_qty") or 1)
        if qty < min_qty:
            raise HTTPException(
                422, f"MinQtyViolation: товар {product_id}, минимальная партия {min_qty}, указано {qty}"
            )
        price = float(product.get("base_price") or 0)
        lines.append((product_id, qty, price, line_total(price, qty)))
    total = round(sum(line[3] for line in lines), 2)

    # 2. Карточка клиента: статус и кредитный лимит (сервис clients).
    card = _load_client_card(client_id, user)
    if card.get("status") == "blocked":
        raise HTTPException(403, "ClientBlocked: клиент заблокирован")
    open_orders = session.exec(
        select(Order).where(
            Order.client_id == client_id, Order.status.in_(CREDIT_EXPOSURE_STATUSES)  # type: ignore[attr-defined]
        )
    ).all()
    exposure = sum(order.total_amount for order in open_orders)
    if exceeds_credit(float(card.get("credit_limit") or 0), exposure, total):
        raise HTTPException(
            409,
            {
                "error": "CreditLimitExceeded",
                "credit_limit": card.get("credit_limit"),
                "open_orders_total": round(exposure, 2),
                "new_order_total": total,
            },
        )

    # 3. Локальная транзакция: заказ создаётся, но ещё не зафиксирован.
    order = Order(
        client_id=client_id,
        delivery_address=data.delivery_address,
        comment=data.comment,
        currency=data.currency,
        total_amount=total,
        status="pending",
    )
    session.add(order)
    session.flush()
    order.number = f"ORD-{datetime.now(timezone.utc):%Y%m%d}-{order.id:05d}"
    session.add(order)
    for product_id, qty, price, amount in lines:
        session.add(
            OrderItem(order_id=order.id, product_id=product_id, qty=qty, price=price, discount=0.0, total=amount)
        )
    _history(session, order.id, "pending", user)
    session.flush()

    # 4. Резерв на складе. При отказе транзакция откатывается (компенсация).
    reserve_payload = {
        "order_id": order.id,
        "items": [{"product_id": pid, "qty": qty} for pid, qty, _, _ in lines],
    }
    try:
        response = call("POST", f"{WAREHOUSE_URL}/warehouse/reserve", user.token, json=reserve_payload, service="warehouse")
    except HTTPException:
        session.rollback()
        raise
    if response.status_code == 409:
        session.rollback()
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text
        raise HTTPException(409, detail)
    if response.status_code not in (200, 201):
        session.rollback()
        raise HTTPException(502, f"Склад не смог выполнить резерв (статус {response.status_code})")

    # 5. Фиксация; если не удалась — снимаем резерв.
    order_id = order.id
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        _release_reserve(order_id, user)
        raise HTTPException(500, f"Не удалось сохранить заказ, резерв снят ({exc.__class__.__name__})")
    session.refresh(order)
    return _serialize(session, order)


# ---------- Просмотр ----------

@router.get("/", summary="Список заказов")
def list_orders(
    status: Optional[str] = None,
    client_id: Optional[int] = None,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    query = select(Order)
    if not has_role(user, *STAFF_READ):
        query = query.where(Order.client_id == user.id)
    elif client_id is not None:
        query = query.where(Order.client_id == client_id)
    if status:
        query = query.where(Order.status == status)
    orders = session.exec(query.order_by(Order.id)).all()
    return [_serialize(session, order) for order in orders]


@router.get("/export", summary="Выгрузка заказов для отчётности")
def export_orders(
    statuses: str = "confirmed,shipped",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user: CurrentUser = Depends(require_roles("manager", "director")),
    session: Session = Depends(get_session),
):
    """Выгрузка заказов с позициями для сервиса отчётности (REST-экспорт)."""
    wanted = [item.strip() for item in statuses.split(",") if item.strip()]
    query = select(Order).where(Order.status.in_(wanted))  # type: ignore[attr-defined]
    if date_from is not None:
        query = query.where(Order.order_date >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to is not None:
        query = query.where(Order.order_date <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    orders = session.exec(query.order_by(Order.id)).all()
    return [_serialize(session, order) for order in orders]


@router.get("/{order_id}", summary="Заказ по ID")
def get_order(
    order_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    order = _get_order(session, order_id)
    _check_read_access(user, order)
    result = _serialize(session, order)
    history = session.exec(
        select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id).order_by(OrderStatusHistory.id)
    ).all()
    result["history"] = [
        {"status": row.status, "changed_by": row.changed_by, "changed_at": row.changed_at.isoformat()}
        for row in history
    ]
    return result


# ---------- UC-06: изменение статуса, отмена, удаление ----------

def _cancel(session: Session, order: Order, user: CurrentUser) -> dict:
    if order.status in ("shipped", "cancelled"):
        raise HTTPException(409, f"Заказ в статусе «{order.status}» нельзя отменить")
    _release_reserve(order.id, user)
    order.status = "cancelled"
    session.add(order)
    _history(session, order.id, "cancelled", user)
    session.commit()
    session.refresh(order)
    return _serialize(session, order)


@router.put("/edit/{order_id}", summary="Изменить заказ или его статус")
def edit_order(
    order_id: int,
    data: OrderEdit,
    user: CurrentUser = Depends(require_roles("manager")),
    session: Session = Depends(get_session),
):
    order = _get_order(session, order_id)
    if order.status == "shipped":
        raise HTTPException(409, "Отгруженный заказ изменять нельзя")
    if order.status == "cancelled":
        raise HTTPException(409, "Отменённый заказ изменять нельзя")

    if data.status is not None and data.status != order.status:
        if data.status == "shipped":
            raise HTTPException(409, "UseWarehouseShipment: статус «shipped» ставится только отгрузкой на складе")
        if not can_edit_status(order.status, data.status):
            raise HTTPException(422, f"Допустимые статусы: {', '.join(EDITABLE_STATUSES)}")
        if data.status == "cancelled":
            if data.delivery_address is not None:
                order.delivery_address = data.delivery_address
            if data.comment is not None:
                order.comment = data.comment
            return _cancel(session, order, user)
        order.status = data.status
        _history(session, order.id, data.status, user)

    if data.delivery_address is not None:
        order.delivery_address = data.delivery_address
    if data.comment is not None:
        order.comment = data.comment
    session.add(order)
    session.commit()
    session.refresh(order)
    return _serialize(session, order)


@router.put("/cancel/{order_id}", summary="Отменить заказ")
def cancel_order(
    order_id: int,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    order = _get_order(session, order_id)
    if not has_role(user, "manager") and order.client_id != user.id:
        raise HTTPException(403, "Можно отменить только свой заказ")
    return _cancel(session, order, user)


@router.put("/shipped/{order_id}", summary="Отметить заказ отгруженным (вызывает склад)")
def mark_shipped(
    order_id: int,
    user: CurrentUser = Depends(require_roles("storekeeper", "manager")),
    session: Session = Depends(get_session),
):
    """Вызывается складом при отгрузке: подтверждённый заказ получает статус shipped."""
    order = _get_order(session, order_id)
    if order.status != "confirmed":
        raise HTTPException(409, f"Отгрузить можно только подтверждённый заказ (сейчас «{order.status}»)")
    order.status = "shipped"
    session.add(order)
    _history(session, order.id, "shipped", user)
    session.commit()
    session.refresh(order)
    return _serialize(session, order)


@router.delete("/delete/{order_id}", status_code=204, summary="Удалить заказ")
def delete_order(
    order_id: int,
    user: CurrentUser = Depends(require_roles()),
    session: Session = Depends(get_session),
):
    order = _get_order(session, order_id)
    if order.status in ("pending", "confirmed"):
        _release_reserve(order.id, user)
    for item in session.exec(select(OrderItem).where(OrderItem.order_id == order_id)).all():
        session.delete(item)
    for row in session.exec(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)).all():
        session.delete(row)
    session.delete(order)
    session.commit()
