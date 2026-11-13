from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database.connection import get_session
from http_client import ORDERS_URL, call, get_product
from logic import plan_reserve
from models.warehouse import (
    Reserve,
    Shipment,
    ShipmentItem,
    Stock,
    StockMovement,
    Warehouse,
)
from security import CurrentUser, get_current_user, has_role, require_roles

router = APIRouter(prefix="/warehouse", tags=["Склад"])

STAFF = ("manager", "admin", "storekeeper", "accountant", "director")


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: Optional[str] = None


class ReceiptIn(BaseModel):
    warehouse_id: int
    product_id: int
    qty: int = Field(gt=0)
    min_qty: Optional[int] = Field(default=None, ge=0)


class WriteoffIn(BaseModel):
    warehouse_id: int
    product_id: int
    qty: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class AdjustIn(BaseModel):
    warehouse_id: int
    product_id: int
    new_qty: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class ReserveItem(BaseModel):
    product_id: int
    qty: int = Field(gt=0)


class ReserveIn(BaseModel):
    order_id: int
    items: List[ReserveItem] = Field(min_length=1)


def _get_active_warehouse(session: Session, warehouse_id: int) -> Warehouse:
    warehouse = session.get(Warehouse, warehouse_id)
    if warehouse is None or not warehouse.is_active:
        raise HTTPException(404, "Склад не найден")
    return warehouse


def _get_stock(session: Session, warehouse_id: int, product_id: int) -> Optional[Stock]:
    return session.exec(
        select(Stock).where(Stock.warehouse_id == warehouse_id, Stock.product_id == product_id)
    ).first()


def _movement(
    session: Session,
    *,
    product_id: int,
    warehouse_id: int,
    type_: str,
    qty: int,
    user: CurrentUser,
    document_type: Optional[str] = None,
    document_id: Optional[int] = None,
    comment: Optional[str] = None,
) -> None:
    session.add(
        StockMovement(
            product_id=product_id,
            warehouse_id=warehouse_id,
            type=type_,
            qty=qty,
            document_type=document_type,
            document_id=document_id,
            comment=comment,
            created_by=user.id,
        )
    )


def _stock_view(stock: Stock) -> dict:
    available = stock.qty - stock.reserved_qty
    return {
        "id": stock.id,
        "warehouse_id": stock.warehouse_id,
        "product_id": stock.product_id,
        "qty": stock.qty,
        "reserved_qty": stock.reserved_qty,
        "available": available,
        "min_qty": stock.min_qty,
        "below_min": available < stock.min_qty,
    }


# ---------- Склады ----------

@router.post("/new", status_code=201, summary="Создать склад")
def create_warehouse(
    data: WarehouseCreate,
    user: CurrentUser = Depends(require_roles("storekeeper")),
    session: Session = Depends(get_session),
):
    if session.exec(select(Warehouse).where(Warehouse.name == data.name.strip())).first():
        raise HTTPException(409, "Склад с таким названием уже есть")
    warehouse = Warehouse(name=data.name.strip(), address=data.address)
    session.add(warehouse)
    session.commit()
    session.refresh(warehouse)
    return warehouse


@router.get("/", summary="Список складов")
def list_warehouses(
    user: CurrentUser = Depends(require_roles(*STAFF)),
    session: Session = Depends(get_session),
):
    return session.exec(select(Warehouse).order_by(Warehouse.id)).all()


# ---------- UC-08: просмотр остатков ----------

@router.get("/stock", summary="Остатки товаров")
def view_stock(
    product_id: Optional[int] = None,
    warehouse_id: Optional[int] = None,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if warehouse_id is not None and session.get(Warehouse, warehouse_id) is None:
        raise HTTPException(404, "Склад не найден")
    query = select(Stock)
    if product_id is not None:
        query = query.where(Stock.product_id == product_id)
    if warehouse_id is not None:
        query = query.where(Stock.warehouse_id == warehouse_id)
    rows = session.exec(query.order_by(Stock.product_id, Stock.warehouse_id)).all()
    if not rows and warehouse_id is not None:
        raise HTTPException(404, "NoWarehouseData: по складу нет данных об остатках")
    if has_role(user, *STAFF):
        return [_stock_view(row) for row in rows]
    # Клиенту показываем только доступное количество (без резервов и порогов).
    return [
        {
            "warehouse_id": row.warehouse_id,
            "product_id": row.product_id,
            "available": row.qty - row.reserved_qty,
        }
        for row in rows
    ]


@router.get("/movements", summary="Журнал движений")
def list_movements(
    product_id: Optional[int] = None,
    warehouse_id: Optional[int] = None,
    type: Optional[str] = None,
    user: CurrentUser = Depends(require_roles("storekeeper", "manager", "director", "accountant")),
    session: Session = Depends(get_session),
):
    query = select(StockMovement)
    if product_id is not None:
        query = query.where(StockMovement.product_id == product_id)
    if warehouse_id is not None:
        query = query.where(StockMovement.warehouse_id == warehouse_id)
    if type:
        query = query.where(StockMovement.type == type)
    return session.exec(query.order_by(StockMovement.id)).all()


# ---------- Приёмка, списание, инвентаризация ----------

@router.post("/receipt", status_code=201, summary="Приёмка товара")
def receipt(
    data: ReceiptIn,
    user: CurrentUser = Depends(require_roles("storekeeper")),
    session: Session = Depends(get_session),
):
    _get_active_warehouse(session, data.warehouse_id)
    try:
        if get_product(user.token, data.product_id) is None:
            raise HTTPException(404, "Товар не найден в каталоге")
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        # Каталог недоступен или ограничил доступ роли — приёмку не блокируем.
    stock = _get_stock(session, data.warehouse_id, data.product_id)
    if stock is None:
        stock = Stock(warehouse_id=data.warehouse_id, product_id=data.product_id)
    stock.qty += data.qty
    if data.min_qty is not None:
        stock.min_qty = data.min_qty
    session.add(stock)
    session.flush()
    _movement(
        session,
        product_id=data.product_id,
        warehouse_id=data.warehouse_id,
        type_="receipt",
        qty=data.qty,
        user=user,
        document_type="receipt",
    )
    session.commit()
    session.refresh(stock)
    return _stock_view(stock)


@router.post("/writeoff", status_code=201, summary="Списание товара")
def writeoff(
    data: WriteoffIn,
    user: CurrentUser = Depends(require_roles("storekeeper")),
    session: Session = Depends(get_session),
):
    """Списание повреждённого или утерянного товара (расширение UC-09)."""
    stock = _get_stock(session, data.warehouse_id, data.product_id)
    if stock is None:
        raise HTTPException(404, "Остатка по товару на этом складе нет")
    if stock.qty - stock.reserved_qty < data.qty:
        raise HTTPException(409, "Нельзя списать больше свободного остатка (часть товара в резерве)")
    stock.qty -= data.qty
    session.add(stock)
    _movement(
        session,
        product_id=data.product_id,
        warehouse_id=data.warehouse_id,
        type_="writeoff",
        qty=-data.qty,
        user=user,
        document_type="writeoff",
        comment=data.reason,
    )
    session.commit()
    session.refresh(stock)
    return _stock_view(stock)


@router.post("/adjust", status_code=201, summary="Инвентаризация (корректировка остатка)")
def adjust(
    data: AdjustIn,
    user: CurrentUser = Depends(require_roles("storekeeper")),
    session: Session = Depends(get_session),
):
    """Корректировка по итогам инвентаризации."""
    stock = _get_stock(session, data.warehouse_id, data.product_id)
    if stock is None:
        raise HTTPException(404, "Остатка по товару на этом складе нет")
    if data.new_qty < stock.reserved_qty:
        raise HTTPException(409, "Новое количество меньше зарезервированного")
    delta = data.new_qty - stock.qty
    stock.qty = data.new_qty
    session.add(stock)
    _movement(
        session,
        product_id=data.product_id,
        warehouse_id=data.warehouse_id,
        type_="adjustment",
        qty=delta,
        user=user,
        document_type="inventory",
        comment=data.reason,
    )
    session.commit()
    session.refresh(stock)
    return _stock_view(stock)


# ---------- UC-09: резерв и отгрузка ----------

@router.post("/reserve", status_code=201, summary="Зарезервировать товары заказа")
def reserve(
    data: ReserveIn,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    """Резервирует товары заказа целиком (всё или ничего)."""
    already = session.exec(
        select(Reserve).where(Reserve.order_id == data.order_id, Reserve.status == "active")
    ).all()
    if already:
        raise HTTPException(409, "ReserveAlreadyExists: резерв для заказа уже создан")

    items: Dict[int, int] = {}
    for item in data.items:
        items[item.product_id] = items.get(item.product_id, 0) + item.qty

    stocks_by_key: Dict[Tuple[int, int], Stock] = {}
    free_by_product: Dict[int, List[Tuple[int, int]]] = {}
    for product_id in items:
        rows = session.exec(
            select(Stock)
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .where(Stock.product_id == product_id, Warehouse.is_active == True)  # noqa: E712
        ).all()
        free_by_product[product_id] = [(row.warehouse_id, row.qty - row.reserved_qty) for row in rows]
        for row in rows:
            stocks_by_key[(row.warehouse_id, row.product_id)] = row

    plan, shortages = plan_reserve(free_by_product, items)
    if shortages:
        raise HTTPException(409, {"error": "InsufficientStock", "shortages": shortages})

    created = []
    for product_id, warehouse_id, qty in plan:
        stocks_by_key[(warehouse_id, product_id)].reserved_qty += qty
        session.add(stocks_by_key[(warehouse_id, product_id)])
        reserve_row = Reserve(
            order_id=data.order_id, product_id=product_id, warehouse_id=warehouse_id, qty=qty
        )
        session.add(reserve_row)
        created.append(reserve_row)
    session.commit()
    for row in created:
        session.refresh(row)
    return created


@router.post("/release/{order_id}", summary="Снять резерв заказа")
def release(
    order_id: int,
    user: CurrentUser = Depends(require_roles("client", "manager", "storekeeper")),
    session: Session = Depends(get_session),
):
    """Снимает активный резерв заказа (отмена заказа или компенсация ошибки). Идемпотентно."""
    reserves = session.exec(
        select(Reserve).where(Reserve.order_id == order_id, Reserve.status == "active")
    ).all()
    for row in reserves:
        stock = _get_stock(session, row.warehouse_id, row.product_id)
        if stock is not None:
            stock.reserved_qty = max(0, stock.reserved_qty - row.qty)
            session.add(stock)
        row.status = "released"
        session.add(row)
    session.commit()
    return {"order_id": order_id, "released": len(reserves)}


@router.get("/reserves/{order_id}", summary="Резервы заказа")
def get_reserves(
    order_id: int,
    user: CurrentUser = Depends(require_roles("storekeeper", "manager", "accountant", "director")),
    session: Session = Depends(get_session),
):
    return session.exec(select(Reserve).where(Reserve.order_id == order_id).order_by(Reserve.id)).all()


@router.post("/ship/{order_id}", status_code=201, summary="Отгрузить заказ")
def ship_order(
    order_id: int,
    user: CurrentUser = Depends(require_roles("storekeeper")),
    session: Session = Depends(get_session),
):
    """Отгрузка: списание зарезервированного товара, накладная и смена статуса заказа."""
    reserves = session.exec(
        select(Reserve).where(Reserve.order_id == order_id, Reserve.status == "active")
    ).all()
    if not reserves:
        raise HTTPException(404, "Активного резерва по заказу нет")

    shipment = Shipment(order_id=order_id, shipped_by=user.id)
    session.add(shipment)
    session.flush()
    shipment.number = f"SHP-{shipment.id:06d}"
    session.add(shipment)

    for row in reserves:
        stock = _get_stock(session, row.warehouse_id, row.product_id)
        if stock is None or stock.qty < row.qty:
            session.rollback()
            raise HTTPException(409, "Фактический остаток меньше резерва: выполните инвентаризацию")
        stock.qty -= row.qty
        stock.reserved_qty = max(0, stock.reserved_qty - row.qty)
        session.add(stock)
        row.status = "shipped"
        session.add(row)
        session.add(
            ShipmentItem(
                shipment_id=shipment.id,
                product_id=row.product_id,
                warehouse_id=row.warehouse_id,
                qty=row.qty,
            )
        )
        _movement(
            session,
            product_id=row.product_id,
            warehouse_id=row.warehouse_id,
            type_="shipment",
            qty=-row.qty,
            user=user,
            document_type="shipment",
            document_id=shipment.id,
        )
    session.flush()

    response = call("PUT", f"{ORDERS_URL}/order/shipped/{order_id}", user.token, service="orders")
    if response.status_code != 200:
        session.rollback()
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text
        code = response.status_code if response.status_code in (404, 409) else 502
        raise HTTPException(code, f"Сервис заказов отклонил отгрузку: {detail}")

    session.commit()
    session.refresh(shipment)
    items = session.exec(select(ShipmentItem).where(ShipmentItem.shipment_id == shipment.id)).all()
    return {"shipment": shipment, "items": items}


@router.get("/shipments/{order_id}", summary="Отгрузки заказа")
def get_shipments(
    order_id: int,
    user: CurrentUser = Depends(require_roles("storekeeper", "manager", "accountant", "director")),
    session: Session = Depends(get_session),
):
    result = []
    for shipment in session.exec(select(Shipment).where(Shipment.order_id == order_id)).all():
        items = session.exec(select(ShipmentItem).where(ShipmentItem.shipment_id == shipment.id)).all()
        result.append({"shipment": shipment, "items": items})
    return result
