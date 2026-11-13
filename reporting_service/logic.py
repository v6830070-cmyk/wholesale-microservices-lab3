"""Чистая логика отчётности: группировка метрик продаж (без FastAPI и БД)."""
from datetime import date
from typing import Dict, Iterable, List

GROUP_BY = ("client", "product", "month")


def group_key(row: dict, group_by: str) -> str:
    if group_by == "client":
        return str(row["client_id"])
    if group_by == "product":
        return str(row["product_id"])
    if group_by == "month":
        return str(row["month"])
    raise ValueError(f"Группировка должна быть одной из: {', '.join(GROUP_BY)}")


def aggregate(rows: Iterable[dict], group_by: str) -> dict:
    """rows: словари с ключами order_id, client_id, product_id, month, qty, revenue, cost, margin."""
    rows = list(rows)
    groups: Dict[str, dict] = {}
    for row in rows:
        key = group_key(row, group_by)
        group = groups.setdefault(
            key, {"key": key, "qty": 0, "revenue": 0.0, "cost": 0.0, "margin": 0.0, "orders": set()}
        )
        group["qty"] += int(row["qty"])
        group["revenue"] += float(row["revenue"])
        group["cost"] += float(row["cost"])
        group["margin"] += float(row["margin"])
        group["orders"].add(row["order_id"])

    result: List[dict] = []
    for group in groups.values():
        revenue = round(group["revenue"], 2)
        margin = round(group["margin"], 2)
        result.append(
            {
                "key": group["key"],
                "orders": len(group["orders"]),
                "qty": group["qty"],
                "revenue": revenue,
                "cost": round(group["cost"], 2),
                "margin": margin,
                "margin_pct": round(margin / revenue * 100, 2) if revenue else 0.0,
            }
        )
    if group_by == "month":
        result.sort(key=lambda item: item["key"])
    else:
        result.sort(key=lambda item: (-item["revenue"], item["key"]))

    all_orders = {row["order_id"] for row in rows}
    revenue_total = round(sum(item["revenue"] for item in result), 2)
    margin_total = round(sum(item["margin"] for item in result), 2)
    totals = {
        "qty": sum(item["qty"] for item in result),
        "revenue": revenue_total,
        "cost": round(sum(item["cost"] for item in result), 2),
        "margin": margin_total,
        "margin_pct": round(margin_total / revenue_total * 100, 2) if revenue_total else 0.0,
        "orders": len(all_orders),
    }
    return {"rows": result, "totals": totals}


def validate_period(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ValueError("Дата начала периода позже даты окончания")
