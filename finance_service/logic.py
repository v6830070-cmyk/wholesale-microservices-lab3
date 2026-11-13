"""Чистая логика финансов: НДС, распределение платежей по счетам (без FastAPI и БД)."""
from typing import List, Tuple

PAYMENT_METHODS = ("bank_transfer", "cash", "card")


def to_cents(value: float) -> int:
    return int(round(value * 100))


def calc_vat(net: float, rate: float) -> Tuple[float, float]:
    """Возвращает (сумма НДС, итого с НДС)."""
    net_cents = to_cents(net)
    vat_cents = int(round(net_cents * rate / 100))
    return vat_cents / 100, (net_cents + vat_cents) / 100


def plan_allocation(payments: List[dict], invoices: List[dict]) -> List[Tuple[int, int, float]]:
    """Закрывает счета свободными суммами платежей в порядке поступления (FIFO).

    payments: [{"id": int, "free": float}], invoices: [{"id": int, "due": float}].
    Возвращает список (payment_id, invoice_id, сумма).
    """
    free = [to_cents(item["free"]) for item in payments]
    result: List[Tuple[int, int, float]] = []
    index = 0
    for invoice in invoices:
        due = to_cents(invoice["due"])
        while due > 0 and index < len(payments):
            if free[index] <= 0:
                index += 1
                continue
            take = min(due, free[index])
            result.append((payments[index]["id"], invoice["id"], take / 100))
            free[index] -= take
            due -= take
    return result


def invoice_status(current: str, paid: float, total: float) -> str:
    if current == "cancelled":
        return current
    if to_cents(paid) >= to_cents(total):
        return "paid"
    if to_cents(paid) > 0:
        return "partially_paid"
    return current if current in ("issued", "sent") else "sent"
