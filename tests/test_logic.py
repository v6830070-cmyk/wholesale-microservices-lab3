"""Автономные тесты чистой логики (не требуют FastAPI, БД и запущенных сервисов).

Запуск из корня проекта:  python -m pytest tests -q
"""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(service: str):
    spec = importlib.util.spec_from_file_location(f"{service}_logic", ROOT / service / "logic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clients = load("clients_service")
warehouse = load("warehouse_service")
orders = load("orders_service")
finance = load("finance_service")
reporting = load("reporting_service")


# ---------- clients ----------
def test_inn_validation():
    assert clients.validate_inn(" 1234567890 ") == "1234567890"
    assert clients.validate_inn("123456789012") == "123456789012"
    for bad in ("123", "12345678901", "12345abcde", ""):
        with pytest.raises(ValueError):
            clients.validate_inn(bad)


def test_kpp_and_limit():
    assert clients.validate_kpp(None) is None
    assert clients.validate_kpp("123456789") == "123456789"
    with pytest.raises(ValueError):
        clients.validate_kpp("12345")
    assert clients.validate_credit_limit(1000.456) == 1000.46
    with pytest.raises(ValueError):
        clients.validate_credit_limit(-1)


# ---------- warehouse ----------
def test_allocate_takes_largest_first_and_splits():
    assert warehouse.allocate([(1, 5), (2, 10)], 8) == [(2, 8)]
    assert warehouse.allocate([(1, 5), (2, 3)], 7) == [(1, 5), (2, 2)]
    assert warehouse.allocate([(1, 5)], 6) == []
    assert warehouse.allocate([(1, 0)], 1) == []


def test_plan_reserve_all_or_nothing_reports_shortage():
    free = {10: [(1, 5), (2, 5)], 20: [(1, 2)]}
    plan, shortages = warehouse.plan_reserve(free, {10: 7, 20: 3})
    assert shortages == [{"product_id": 20, "requested": 3, "available": 2}]
    plan, shortages = warehouse.plan_reserve(free, {10: 7, 20: 2})
    assert shortages == []
    assert sum(q for p, _, q in plan if p == 10) == 7
    assert sum(q for p, _, q in plan if p == 20) == 2
    plan, shortages = warehouse.plan_reserve(free, {99: 1})
    assert shortages == [{"product_id": 99, "requested": 1, "available": 0}]


# ---------- orders ----------
def test_merge_items_and_totals():
    assert orders.merge_items([(1, 2), (2, 1), (1, 3)]) == {1: 5, 2: 1}
    assert orders.line_total(10.5, 3) == 31.5
    assert orders.line_total(10.0, 3, 5.0) == 25.0


def test_credit_limit_rule():
    assert orders.exceeds_credit(0, 1_000_000, 1_000_000) is False  # лимит не задан
    assert orders.exceeds_credit(1000, 600, 400) is False  # ровно в лимит
    assert orders.exceeds_credit(1000, 600, 400.01) is True


def test_status_transitions():
    assert orders.can_edit_status("pending", "confirmed")
    assert orders.can_edit_status("confirmed", "pending")
    assert orders.can_edit_status("pending", "cancelled")
    assert not orders.can_edit_status("pending", "shipped")
    assert not orders.can_edit_status("shipped", "confirmed")
    assert not orders.can_edit_status("cancelled", "pending")


# ---------- finance ----------
def test_vat():
    assert finance.calc_vat(1000.0, 20) == (200.0, 1200.0)
    assert finance.calc_vat(99.99, 20) == (20.0, 119.99)
    assert finance.calc_vat(100.0, 0) == (0.0, 100.0)


def test_allocation_partial_and_overpay():
    payments = [{"id": 1, "free": 500.0}]
    invoices = [{"id": 10, "due": 1200.0}]
    assert finance.plan_allocation(payments, invoices) == [(1, 10, 500.0)]

    payments = [{"id": 1, "free": 500.0}, {"id": 2, "free": 1000.0}]
    invoices = [{"id": 10, "due": 1200.0}]
    plan = finance.plan_allocation(payments, invoices)
    assert plan == [(1, 10, 500.0), (2, 10, 700.0)]

    # переплата: лишние 300 остаются свободными (в план не попадают)
    payments = [{"id": 1, "free": 1500.0}]
    invoices = [{"id": 10, "due": 1200.0}]
    assert finance.plan_allocation(payments, invoices) == [(1, 10, 1200.0)]

    # несколько счетов закрываются по порядку
    payments = [{"id": 1, "free": 1500.0}]
    invoices = [{"id": 10, "due": 1000.0}, {"id": 11, "due": 1000.0}]
    assert finance.plan_allocation(payments, invoices) == [(1, 10, 1000.0), (1, 11, 500.0)]

    assert finance.plan_allocation([], invoices) == []
    assert finance.plan_allocation([{"id": 1, "free": 0.0}], invoices) == []


def test_invoice_status():
    assert finance.invoice_status("sent", 0, 100) == "sent"
    assert finance.invoice_status("issued", 0, 100) == "issued"
    assert finance.invoice_status("sent", 40, 100) == "partially_paid"
    assert finance.invoice_status("partially_paid", 100, 100) == "paid"
    assert finance.invoice_status("cancelled", 100, 100) == "cancelled"


# ---------- reporting ----------
ROWS = [
    {"order_id": 1, "client_id": 5, "product_id": 100, "month": "2026-10", "qty": 2, "revenue": 200.0, "cost": 120.0, "margin": 80.0},
    {"order_id": 1, "client_id": 5, "product_id": 101, "month": "2026-10", "qty": 1, "revenue": 50.0, "cost": 30.0, "margin": 20.0},
    {"order_id": 2, "client_id": 6, "product_id": 100, "month": "2026-11", "qty": 3, "revenue": 300.0, "cost": 180.0, "margin": 120.0},
]


def test_aggregate_by_month_product_client():
    by_month = reporting.aggregate(ROWS, "month")
    assert [r["key"] for r in by_month["rows"]] == ["2026-10", "2026-11"]
    assert by_month["rows"][0]["revenue"] == 250.0
    assert by_month["totals"]["revenue"] == 550.0
    assert by_month["totals"]["orders"] == 2

    by_product = reporting.aggregate(ROWS, "product")
    assert by_product["rows"][0]["key"] == "100"  # самая большая выручка первой
    assert by_product["rows"][0]["qty"] == 5
    assert by_product["rows"][0]["margin_pct"] == 40.0

    by_client = reporting.aggregate(ROWS, "client")
    assert {r["key"] for r in by_client["rows"]} == {"5", "6"}
    assert by_client["totals"]["margin"] == 220.0

    with pytest.raises(ValueError):
        reporting.aggregate(ROWS, "weekday")


def test_period_validation():
    reporting.validate_period(date(2026, 1, 1), date(2026, 1, 1))
    with pytest.raises(ValueError):
        reporting.validate_period(date(2026, 2, 1), date(2026, 1, 1))
