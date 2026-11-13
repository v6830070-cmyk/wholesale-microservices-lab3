from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database.connection import get_session
from http_client import CLIENTS_URL, ORDERS_URL, call, get_product
from logic import PAYMENT_METHODS, calc_vat, invoice_status, plan_allocation
from models.finance import ClientBalance, Invoice, InvoiceItem, Payment, PaymentAllocation
from security import CurrentUser, get_current_user, has_role, require_roles

router = APIRouter(prefix="/finance", tags=["Финансы"])

STAFF = ("accountant", "manager", "director")
CLIENT_VISIBLE_INVOICE_STATUSES = ("sent", "partially_paid", "paid")


class InvoiceCreate(BaseModel):
    order_id: int
    vat_rate: float = Field(default=20.0, ge=0, le=30)
    due_days: int = Field(default=14, ge=1, le=90)


class PaymentCreate(BaseModel):
    client_id: int
    amount: float = Field(gt=0)
    payment_date: Optional[date] = None
    method: str = "bank_transfer"
    external_doc_number: Optional[str] = Field(default=None, max_length=100)


def _get_invoice(session: Session, invoice_id: int) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Счёт не найден")
    return invoice


def _invoice_view(session: Session, invoice: Invoice, with_items: bool = False) -> dict:
    result = invoice.model_dump(mode="json")
    result["due"] = round(invoice.total - invoice.paid_amount, 2)
    if with_items:
        items = session.exec(select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id)).all()
        result["items"] = [item.model_dump(mode="json") for item in items]
    return result


def recalc_balance(session: Session, client_id: int) -> ClientBalance:
    invoices = session.exec(
        select(Invoice).where(Invoice.client_id == client_id, Invoice.status != "cancelled")
    ).all()
    payments = session.exec(select(Payment).where(Payment.client_id == client_id)).all()
    invoiced = round(sum(inv.total for inv in invoices), 2)
    paid = round(sum(pay.amount for pay in payments), 2)
    debt = round(sum(inv.total - inv.paid_amount for inv in invoices), 2)
    advance = round(sum(pay.amount - pay.allocated_amount for pay in payments), 2)
    balance = session.get(ClientBalance, client_id)
    if balance is None:
        balance = ClientBalance(client_id=client_id)
    balance.invoiced_total = invoiced
    balance.paid_total = paid
    balance.balance = round(paid - invoiced, 2)
    balance.debt = max(debt, 0.0)
    balance.advance = max(advance, 0.0)
    balance.updated_at = datetime.now(timezone.utc)
    session.add(balance)
    return balance


def allocate_client(session: Session, client_id: int) -> List[PaymentAllocation]:
    """Распределяет свободные деньги клиента по неоплаченным счетам (FIFO)."""
    payments = session.exec(
        select(Payment).where(Payment.client_id == client_id).order_by(Payment.payment_date, Payment.id)
    ).all()
    invoices = session.exec(
        select(Invoice)
        .where(Invoice.client_id == client_id, Invoice.status != "cancelled")
        .order_by(Invoice.issue_date, Invoice.id)
    ).all()
    open_invoices = [inv for inv in invoices if round(inv.total - inv.paid_amount, 2) > 0]
    free_payments = [pay for pay in payments if round(pay.amount - pay.allocated_amount, 2) > 0]
    plan = plan_allocation(
        [{"id": pay.id, "free": pay.amount - pay.allocated_amount} for pay in free_payments],
        [{"id": inv.id, "due": inv.total - inv.paid_amount} for inv in open_invoices],
    )
    payments_by_id = {pay.id: pay for pay in free_payments}
    invoices_by_id = {inv.id: inv for inv in open_invoices}
    created: List[PaymentAllocation] = []
    for payment_id, invoice_id, amount in plan:
        payment = payments_by_id[payment_id]
        invoice = invoices_by_id[invoice_id]
        payment.allocated_amount = round(payment.allocated_amount + amount, 2)
        invoice.paid_amount = round(invoice.paid_amount + amount, 2)
        invoice.status = invoice_status(invoice.status, invoice.paid_amount, invoice.total)
        allocation = PaymentAllocation(payment_id=payment_id, invoice_id=invoice_id, amount=amount)
        session.add(allocation)
        session.add(payment)
        session.add(invoice)
        created.append(allocation)
    session.flush()
    recalc_balance(session, client_id)
    return created


# ---------- UC-10: счета ----------

@router.post("/invoice/new", status_code=201, summary="Выставить счёт на заказ")
def create_invoice(
    data: InvoiceCreate,
    user: CurrentUser = Depends(require_roles("accountant")),
    session: Session = Depends(get_session),
):
    order_response = call("GET", f"{ORDERS_URL}/order/{data.order_id}", user.token, service="orders")
    if order_response.status_code == 404:
        raise HTTPException(404, "Заказ не найден")
    if order_response.status_code != 200:
        raise HTTPException(502, f"Сервис заказов вернул статус {order_response.status_code}")
    order = order_response.json()
    if order["status"] not in ("confirmed", "shipped"):
        raise HTTPException(409, f"Счёт выставляется на подтверждённый или отгруженный заказ (сейчас «{order['status']}»)")

    existing = session.exec(
        select(Invoice).where(Invoice.order_id == data.order_id, Invoice.status != "cancelled")
    ).first()
    if existing:
        raise HTTPException(409, f"InvoiceAlreadyExists: счёт {existing.number} уже выставлен")

    card_response = call(
        "GET", f"{CLIENTS_URL}/client/by-user/{order['client_id']}", user.token, service="clients"
    )
    if card_response.status_code == 404:
        raise HTTPException(409, "ClientCardRequired: у клиента нет карточки с реквизитами")
    if card_response.status_code != 200:
        raise HTTPException(502, f"Сервис клиентов вернул статус {card_response.status_code}")
    card = card_response.json()

    net = round(float(order["total_amount"]), 2)
    vat_amount, total = calc_vat(net, data.vat_rate)
    today = date.today()
    invoice = Invoice(
        order_id=data.order_id,
        client_id=order["client_id"],
        client_name=card.get("name"),
        client_inn=card.get("inn"),
        client_kpp=card.get("kpp"),
        client_address=card.get("legal_address"),
        issue_date=today,
        due_date=today + timedelta(days=data.due_days),
        amount_net=net,
        vat_rate=data.vat_rate,
        vat_amount=vat_amount,
        total=total,
        created_by=user.id,
    )
    session.add(invoice)
    session.flush()
    invoice.number = f"INV-{today:%Y}-{invoice.id:05d}"
    session.add(invoice)

    for item in order["items"]:
        name = f"Товар {item['product_id']}"
        try:
            product = get_product(user.token, item["product_id"])
            if product and product.get("name"):
                name = product["name"]
        except HTTPException:
            pass
        session.add(
            InvoiceItem(
                invoice_id=invoice.id,
                product_id=item["product_id"],
                name=name,
                qty=item["qty"],
                price=item["price"],
                total=item["total"],
            )
        )
    session.flush()
    # Если у клиента есть неразнесённые деньги (аванс), они сразу закрывают новый счёт.
    allocate_client(session, invoice.client_id)
    session.commit()
    session.refresh(invoice)
    return _invoice_view(session, invoice, with_items=True)


@router.post("/invoice/send/{invoice_id}", summary="Отправить счёт клиенту")
def send_invoice(
    invoice_id: int,
    user: CurrentUser = Depends(require_roles("accountant")),
    session: Session = Depends(get_session),
):
    """«Отправка» клиенту: счёт становится доступен клиенту в личном кабинете."""
    invoice = _get_invoice(session, invoice_id)
    if invoice.status == "cancelled":
        raise HTTPException(409, "Счёт отменён")
    if invoice.status == "issued":
        invoice.status = "sent"
        invoice.sent_at = datetime.now(timezone.utc)
        session.add(invoice)
        session.commit()
        session.refresh(invoice)
    return _invoice_view(session, invoice)


@router.put("/invoice/cancel/{invoice_id}", summary="Отменить счёт")
def cancel_invoice(
    invoice_id: int,
    user: CurrentUser = Depends(require_roles("accountant")),
    session: Session = Depends(get_session),
):
    invoice = _get_invoice(session, invoice_id)
    if invoice.status == "cancelled":
        raise HTTPException(409, "Счёт уже отменён")
    if invoice.paid_amount > 0:
        raise HTTPException(409, "По счёту есть оплата: отмена невозможна")
    invoice.status = "cancelled"
    session.add(invoice)
    recalc_balance(session, invoice.client_id)
    session.commit()
    session.refresh(invoice)
    return _invoice_view(session, invoice)


@router.get("/invoice/", summary="Список счетов")
def list_invoices(
    order_id: Optional[int] = None,
    client_id: Optional[int] = None,
    status: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    query = select(Invoice)
    if has_role(user, *STAFF):
        if client_id is not None:
            query = query.where(Invoice.client_id == client_id)
    else:
        query = query.where(
            Invoice.client_id == user.id,
            Invoice.status.in_(CLIENT_VISIBLE_INVOICE_STATUSES),  # type: ignore[attr-defined]
        )
    if order_id is not None:
        query = query.where(Invoice.order_id == order_id)
    if status:
        query = query.where(Invoice.status == status)
    return [_invoice_view(session, inv) for inv in session.exec(query.order_by(Invoice.id)).all()]


@router.get("/invoice/{invoice_id}", summary="Счёт по ID")
def get_invoice(
    invoice_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    invoice = _get_invoice(session, invoice_id)
    if not has_role(user, *STAFF):
        if invoice.client_id != user.id or invoice.status not in CLIENT_VISIBLE_INVOICE_STATUSES:
            raise HTTPException(403, "Недостаточно прав")
    return _invoice_view(session, invoice, with_items=True)


# ---------- UC-11: платежи ----------

@router.post("/payment/new", status_code=201, summary="Внести платёж")
def register_payment(
    data: PaymentCreate,
    user: CurrentUser = Depends(require_roles("accountant")),
    session: Session = Depends(get_session),
):
    if data.method not in PAYMENT_METHODS:
        raise HTTPException(422, f"Способ оплаты: {', '.join(PAYMENT_METHODS)}")
    payment_date = data.payment_date or date.today()
    if payment_date > date.today():
        raise HTTPException(422, "Дата платежа не может быть в будущем")

    card_response = call("GET", f"{CLIENTS_URL}/client/by-user/{data.client_id}", user.token, service="clients")
    if card_response.status_code == 404:
        raise HTTPException(404, "ClientNotFound: у пользователя нет карточки клиента")
    if card_response.status_code != 200:
        raise HTTPException(502, f"Сервис клиентов вернул статус {card_response.status_code}")

    if data.external_doc_number:
        duplicate = session.exec(
            select(Payment).where(
                Payment.client_id == data.client_id,
                Payment.external_doc_number == data.external_doc_number,
            )
        ).first()
        if duplicate:
            raise HTTPException(409, "DuplicatePayment: платёж с таким номером документа уже внесён")

    payment = Payment(
        client_id=data.client_id,
        payment_date=payment_date,
        amount=round(data.amount, 2),
        method=data.method,
        external_doc_number=data.external_doc_number,
        created_by=user.id,
    )
    session.add(payment)
    session.flush()
    allocate_client(session, data.client_id)
    session.commit()
    session.refresh(payment)
    allocations = session.exec(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
    ).all()
    return {
        "payment": payment.model_dump(mode="json"),
        "allocations": [row.model_dump(mode="json") for row in allocations],
        "unallocated": round(payment.amount - payment.allocated_amount, 2),
    }


@router.get("/payment/", summary="Список платежей")
def list_payments(
    client_id: Optional[int] = None,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    query = select(Payment)
    if has_role(user, *STAFF):
        if client_id is not None:
            query = query.where(Payment.client_id == client_id)
    else:
        query = query.where(Payment.client_id == user.id)
    return session.exec(query.order_by(Payment.id)).all()


@router.get("/payment/{payment_id}", summary="Платёж по ID")
def get_payment(
    payment_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(404, "Платёж не найден")
    if not has_role(user, *STAFF) and payment.client_id != user.id:
        raise HTTPException(403, "Недостаточно прав")
    allocations = session.exec(select(PaymentAllocation).where(PaymentAllocation.payment_id == payment_id)).all()
    return {"payment": payment, "allocations": allocations}


@router.get("/balance/{client_id}", summary="Баланс клиента")
def get_balance(
    client_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if client_id != user.id and not has_role(user, *STAFF):
        raise HTTPException(403, "Недостаточно прав")
    balance = recalc_balance(session, client_id)
    session.commit()
    session.refresh(balance)
    open_invoices = session.exec(
        select(Invoice).where(
            Invoice.client_id == client_id, Invoice.status.in_(["issued", "sent", "partially_paid"])  # type: ignore[attr-defined]
        )
    ).all()
    result = balance.model_dump(mode="json")
    result["open_invoices"] = [inv.number for inv in open_invoices]
    return result
