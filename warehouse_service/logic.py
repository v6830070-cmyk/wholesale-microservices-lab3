"""Чистая логика склада: распределение резерва по складам (без FastAPI и БД)."""
from typing import Dict, List, Tuple


def allocate(free_by_warehouse: List[Tuple[int, int]], qty: int) -> List[Tuple[int, int]]:
    """Распределяет qty по складам, начиная с наибольшего свободного остатка.

    free_by_warehouse: список (warehouse_id, свободное_количество).
    Возвращает список (warehouse_id, количество) либо пустой список, если остатка не хватает.
    """
    candidates = sorted(
        ((wid, free) for wid, free in free_by_warehouse if free > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    if sum(free for _, free in candidates) < qty:
        return []
    result: List[Tuple[int, int]] = []
    remaining = qty
    for wid, free in candidates:
        if remaining == 0:
            break
        take = min(free, remaining)
        result.append((wid, take))
        remaining -= take
    return result


def plan_reserve(
    free_by_product: Dict[int, List[Tuple[int, int]]],
    items: Dict[int, int],
):
    """Планирует резерв всего заказа целиком.

    Возвращает (план, нехватки): план — список (product_id, warehouse_id, qty),
    нехватки — список словарей {product_id, requested, available}.
    Если нехватки не пусты, резерв выполнять нельзя (всё или ничего).
    """
    plan: List[Tuple[int, int, int]] = []
    shortages: List[dict] = []
    for product_id, qty in items.items():
        free_list = free_by_product.get(product_id, [])
        parts = allocate(free_list, qty)
        if not parts:
            shortages.append(
                {
                    "product_id": product_id,
                    "requested": qty,
                    "available": sum(free for _, free in free_list if free > 0),
                }
            )
            continue
        for wid, part in parts:
            plan.append((product_id, wid, part))
    return plan, shortages
