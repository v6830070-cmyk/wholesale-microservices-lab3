import json
from datetime import date, datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database.connection import get_session
from http_client import ORDERS_URL, call, get_product
from logic import GROUP_BY, aggregate, validate_period
from models.reporting import MetricSale, Report
from security import CurrentUser, require_roles

router = APIRouter(prefix="/reporting", tags=["Отчётность"])

REPORT_ROLES = ("director", "manager", "accountant")


class SyncIn(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class SalesReportIn(BaseModel):
    date_from: date
    date_to: date
    group_by: str = "month"
    save: bool = False
    name: Optional[str] = Field(default=None, max_length=255)


def _build_report(session: Session, date_from: date, date_to: date, group_by: str) -> dict:
    if group_by not in GROUP_BY:
        raise HTTPException(422, f"group_by должен быть одним из: {', '.join(GROUP_BY)}")
    try:
        validate_period(date_from, date_to)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    metrics = session.exec(
        select(MetricSale).where(MetricSale.order_date >= date_from, MetricSale.order_date <= date_to)
    ).all()
    if not metrics:
        raise HTTPException(404, "NoData: за выбранный период данных нет")
    rows = [
        {
            "order_id": m.order_id,
            "client_id": m.client_id,
            "product_id": m.product_id,
            "month": m.month,
            "qty": m.qty,
            "revenue": m.revenue,
            "cost": m.cost,
            "margin": m.margin,
        }
        for m in metrics
    ]
    result = aggregate(rows, group_by)
    result.update(
        {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "group_by": group_by}
    )
    return result


# ---------- Загрузка метрик из сервиса заказов ----------

@router.post("/sync", summary="Загрузить метрики продаж из заказов")
def sync_metrics(
    data: SyncIn,
    user: CurrentUser = Depends(require_roles("manager", "director")),
    session: Session = Depends(get_session),
):
    """Забирает подтверждённые и отгруженные заказы через REST и обновляет метрики продаж."""
    params: Dict[str, str] = {"statuses": "confirmed,shipped,cancelled"}
    if data.date_from:
        params["date_from"] = data.date_from.isoformat()
    if data.date_to:
        params["date_to"] = data.date_to.isoformat()
    response = call("GET", f"{ORDERS_URL}/order/export", user.token, params=params, service="orders")
    if response.status_code != 200:
        raise HTTPException(502, f"Сервис заказов вернул статус {response.status_code}")
    orders = response.json()

    cost_cache: Dict[int, float] = {}

    def unit_cost(product_id: int) -> float:
        if product_id not in cost_cache:
            product = get_product(user.token, product_id)
            cost_cache[product_id] = float((product or {}).get("cost_price") or 0)
        return cost_cache[product_id]

    upserted = 0
    deleted = 0
    for order in orders:
        existing = {
            row.product_id: row
            for row in session.exec(select(MetricSale).where(MetricSale.order_id == order["id"])).all()
        }
        if order["status"] == "cancelled":
            for row in existing.values():
                session.delete(row)
                deleted += 1
            continue
        order_date = datetime.fromisoformat(order["order_date"]).date()
        seen = set()
        for item in order["items"]:
            product_id = item["product_id"]
            seen.add(product_id)
            revenue = round(float(item["total"]), 2)
            cost = round(unit_cost(product_id) * item["qty"], 2)
            row = existing.get(product_id) or MetricSale(
                order_id=order["id"],
                product_id=product_id,
                client_id=order["client_id"],
                order_date=order_date,
                month=order_date.strftime("%Y-%m"),
                qty=item["qty"],
                revenue=revenue,
                cost=cost,
                margin=round(revenue - cost, 2),
            )
            row.client_id = order["client_id"]
            row.order_date = order_date
            row.month = order_date.strftime("%Y-%m")
            row.qty = item["qty"]
            row.revenue = revenue
            row.cost = cost
            row.margin = round(revenue - cost, 2)
            row.exported_at = datetime.now(timezone.utc)
            session.add(row)
            upserted += 1
        for product_id, row in existing.items():
            if product_id not in seen:
                session.delete(row)
                deleted += 1
    session.commit()
    return {"orders_processed": len(orders), "rows_upserted": upserted, "rows_deleted": deleted}


# ---------- UC-12: отчёт по продажам ----------

@router.post("/sales", summary="Отчёт по продажам")
def sales_report(
    data: SalesReportIn,
    user: CurrentUser = Depends(require_roles(*REPORT_ROLES)),
    session: Session = Depends(get_session),
):
    result = _build_report(session, data.date_from, data.date_to, data.group_by)
    if data.save:
        parameters = {
            "date_from": data.date_from.isoformat(),
            "date_to": data.date_to.isoformat(),
            "group_by": data.group_by,
        }
        report = Report(
            name=data.name or f"Продажи {data.date_from}–{data.date_to} по {data.group_by}",
            parameters=json.dumps(parameters),
            created_by=user.id,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        result["saved_report_id"] = report.id
    return result


@router.get("/saved", summary="Сохранённые отчёты")
def list_saved(
    user: CurrentUser = Depends(require_roles(*REPORT_ROLES)),
    session: Session = Depends(get_session),
):
    reports = session.exec(select(Report).order_by(Report.id)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "parameters": json.loads(r.parameters),
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in reports
    ]


@router.post("/saved/{report_id}/run", summary="Запустить сохранённый отчёт")
def run_saved(
    report_id: int,
    user: CurrentUser = Depends(require_roles(*REPORT_ROLES)),
    session: Session = Depends(get_session),
):
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Сохранённый отчёт не найден")
    params = json.loads(report.parameters)
    return _build_report(
        session,
        date.fromisoformat(params["date_from"]),
        date.fromisoformat(params["date_to"]),
        params["group_by"],
    )


@router.delete("/saved/{report_id}", status_code=204, summary="Удалить сохранённый отчёт")
def delete_saved(
    report_id: int,
    user: CurrentUser = Depends(require_roles("director")),
    session: Session = Depends(get_session),
):
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Сохранённый отчёт не найден")
    session.delete(report)
    session.commit()
