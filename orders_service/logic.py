"""Чистая логика заказов (без FastAPI и БД)."""
from typing import Dict, Iterable, Tuple

ALLOWED_STATUSES = ("pending", "confirmed", "cancelled", "shipped")
EDITABLE_STATUSES = ("pending", "confirmed", "cancelled")  # shipped ставится только складом
CREDIT_EXPOSURE_STATUSES = ("pending", "confirmed")


def merge_items(items: Iterable[Tuple[int, int]]) -> Dict[int, int]:
    """Объединяет повторяющиеся позиции одного товара."""
    merged: Dict[int, int] = {}
    for product_id, qty in items:
        merged[product_id] = merged.get(product_id, 0) + qty
    return merged


def line_total(price: float, qty: int, discount: float = 0.0) -> float:
    return round(price * qty - discount, 2)


def exceeds_credit(limit: float, exposure: float, new_total: float) -> bool:
    """Кредитный лимит 0 означает «лимит не задан». Иначе сумма открытых заказов
    плюс новый заказ не должна превышать лимит."""
    if limit <= 0:
        return False
    return round(exposure + new_total, 2) > round(limit, 2)


def can_edit_status(current: str, new: str) -> bool:
    if current in ("shipped", "cancelled"):
        return False
    return new in EDITABLE_STATUSES
