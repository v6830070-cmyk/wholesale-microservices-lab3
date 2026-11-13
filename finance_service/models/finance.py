from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    number: Optional[str] = Field(default=None, index=True)
    order_id: int = Field(index=True)
    client_id: int = Field(index=True)  # id пользователя IAM
    client_name: Optional[str] = None
    client_inn: Optional[str] = None
    client_kpp: Optional[str] = None
    client_address: Optional[str] = None
    issue_date: date = Field(default_factory=lambda: date.today())
    due_date: date
    amount_net: float
    vat_rate: float = 20.0
    vat_amount: float = 0.0
    total: float
    paid_amount: float = 0.0
    status: str = "issued"  # issued / sent / partially_paid / paid / cancelled
    sent_at: Optional[datetime] = None
    created_by: Optional[int] = None


class InvoiceItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    product_id: int
    name: str
    qty: int
    price: float
    total: float


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(index=True)
    payment_date: date
    amount: float
    method: str = "bank_transfer"
    external_doc_number: Optional[str] = None
    allocated_amount: float = 0.0
    created_by: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)


class PaymentAllocation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    payment_id: int = Field(foreign_key="payment.id", index=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    amount: float


class ClientBalance(SQLModel, table=True):
    client_id: int = Field(primary_key=True)
    invoiced_total: float = 0.0
    paid_total: float = 0.0
    balance: float = 0.0  # оплачено минус выставлено
    debt: float = 0.0  # неоплаченная часть счетов
    advance: float = 0.0  # нераспределённые деньги клиента
    updated_at: datetime = Field(default_factory=utcnow)
