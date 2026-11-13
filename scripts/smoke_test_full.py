"""Сквозной тест всех сервисов через API Gateway (лаб. 3, полная версия).

Перед запуском должны работать: gateway и сервисы iam, catalog, orders, clients, warehouse, finance, reporting.
В каталоге должен быть хотя бы один товар. Запуск из корня проекта:  python scripts/smoke_test_full.py
Адреса IAM/каталога читаются из их openapi.json, поэтому точные пути регистрации/входа знать не нужно.
"""
import os
import sys
import time

import httpx
import jwt

GATEWAY = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
IAM_DIRECT = os.getenv("IAM_URL", "http://127.0.0.1:8003")
CATALOG_DIRECT = os.getenv("CATALOG_URL", "http://127.0.0.1:8001")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin12345")
PASSWORD = "Password123"
RUN = str(int(time.time()))[-7:]

http = httpx.Client(timeout=20.0)
passed = 0
failed = 0


def check(name, condition, extra=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def section(title):
    print(f"\n== {title}")


def api(method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return http.request(method, GATEWAY + path, headers=headers, **kwargs)


def detail_text(response):
    try:
        return str(response.json().get("detail"))
    except Exception:
        return response.text


# ---------- обнаружение маршрутов IAM ----------
def resolve(spec, schema):
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return spec["components"]["schemas"][name]
    return schema


def find_operation(spec, word, method):
    for path, operations in spec["paths"].items():
        if word in path.lower() and method in operations:
            return path, operations[method]
    raise SystemExit(f"В openapi IAM не найден маршрут {method.upper()} с «{word}»")


def body_kind_and_fields(spec, operation):
    content = operation.get("requestBody", {}).get("content", {})
    for kind in ("application/json", "application/x-www-form-urlencoded"):
        if kind in content:
            schema = resolve(spec, content[kind]["schema"])
            return kind, schema.get("properties", {}), set(schema.get("required", []))
    return None, {}, set()


_counter = [0]


def fill(properties, required, values):
    result = {}
    for name, definition in properties.items():
        lower = name.lower()
        if name in values:
            result[name] = values[name]
        elif name in required or lower in ("inn", "kpp"):
            kind = definition.get("type", "string")
            if "inn" in lower:
                _counter[0] += 1
                result[name] = "55" + RUN + str(_counter[0] % 10)
            elif "kpp" in lower:
                result[name] = "550101001"
            elif "phone" in lower:
                result[name] = "+79000000000"
            else:
                result[name] = {"integer": 1, "number": 1, "boolean": True}.get(kind, "Test")
    return result


spec = httpx.get(f"{IAM_DIRECT}/openapi.json").json()
REG_PATH, REG_OP = find_operation(spec, "register", "post")
LOGIN_PATH, LOGIN_OP = find_operation(spec, "login", "post")
ROLE_PATH, ROLE_OP = find_operation(spec, "role", "put")


def register(email, role_name):
    kind, props, req = body_kind_and_fields(spec, REG_OP)
    values = {"email": email, "password": PASSWORD, "username": email,
              "full_name": role_name, "name": role_name}
    payload = fill(props, req | ({"email", "password"} & set(props)), values)
    response = api("POST", "/iam" + REG_PATH, **({"json": payload} if kind == "application/json" else {"data": payload}))
    return response


def login(email, password):
    kind, props, req = body_kind_and_fields(spec, LOGIN_OP)
    values = {"email": email, "username": email, "password": password}
    payload = fill(props, req | ({"password"} & set(props)), values)
    if "email" in props:
        payload["email"] = email
    if "username" in props:
        payload["username"] = email
    response = api("POST", "/iam" + LOGIN_PATH, **({"json": payload} if kind == "application/json" else {"data": payload}))
    if response.status_code != 200:
        raise SystemExit(f"Вход не удался для {email}: {response.status_code} {response.text}")
    data = response.json()
    return data.get("access_token") or data.get("token")


def user_id_from_token(token):
    payload = jwt.decode(token, options={"verify_signature": False})
    for key in ("user_id", "uid", "id", "sub"):
        if key in payload:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
    raise SystemExit("В токене не найден id пользователя")


def set_role(admin_token, user_id, role_name):
    path = ROLE_PATH
    for part in ("{user_id}", "{id}", "{uid}"):
        path = path.replace(part, str(user_id))
    kind, props, _ = body_kind_and_fields(spec, ROLE_OP)
    if props:
        payload = {"role": role_name} if "role" in props else {next(iter(props)): role_name}
        response = api("PUT", "/iam" + path, admin_token, json=payload)
    else:
        response = api("PUT", "/iam" + path, admin_token, params={"role": role_name})
    return response


def new_user(admin_token, role_name):
    email = f"{role_name}_{RUN}@test.local"
    response = register(email, role_name)
    if response.status_code not in (200, 201):
        raise SystemExit(f"Регистрация {email} не удалась: {response.status_code} {response.text}")
    first_token = login(email, PASSWORD)
    uid = user_id_from_token(first_token)
    if role_name != "client":
        result = set_role(admin_token, uid, role_name)
        if result.status_code != 200:
            raise SystemExit(
                f"Не удалось назначить роль {role_name}: {result.status_code} {result.text}\n"
                "Добавьте эту роль в список ROLES сервиса iam_service."
            )
    return uid, login(email, PASSWORD)


def pick_product():
    cspec = httpx.get(f"{CATALOG_DIRECT}/openapi.json").json()
    for path, operations in cspec["paths"].items():
        if "product" in path.lower() and "{" not in path and "get" in operations:
            response = api("GET", "/catalog" + (path if path.endswith("/") else path + "/"), ADMIN_TOKEN)
            if response.status_code == 200:
                data = response.json()
                items = data if isinstance(data, list) else data.get("items", [])
                for item in items:
                    if float(item.get("base_price") or 0) > 0:
                        return item
    raise SystemExit("В каталоге нет товара с ценой: создайте товар и повторите тест")


# =====================================================================
section("0. Подготовка: шлюз, пользователи, товар")
health = httpx.get(f"{GATEWAY}/health").json()
check("шлюз видит все сервисы", all(v == "up" for v in health["services"].values()), str(health))
ADMIN_TOKEN = login(ADMIN_EMAIL, ADMIN_PASSWORD)

_, MANAGER = new_user(ADMIN_TOKEN, "manager")
_, STORE = new_user(ADMIN_TOKEN, "storekeeper")
_, ACCT = new_user(ADMIN_TOKEN, "accountant")
_, DIRECTOR = new_user(ADMIN_TOKEN, "director")
CLIENT_A_ID, CLIENT_A = new_user(ADMIN_TOKEN, "client")
email_b = f"clientb_{RUN}@test.local"
check("регистрация второго клиента", register(email_b, "client").status_code in (200, 201))
CLIENT_B = login(email_b, PASSWORD)
CLIENT_B_ID = user_id_from_token(CLIENT_B)

product = pick_product()
PID = product["id"]
PRICE = float(product["base_price"])
MIN_QTY = int(product.get("min_qty") or 1)
QTY = max(MIN_QTY, 2)
print(f"  товар id={PID}, цена={PRICE}, min_qty={MIN_QTY}")

check("без токена шлюз отвечает 401", api("GET", "/orders/order/").status_code == 401)

# =====================================================================
section("1. Склад: приёмка и права (UC-08)")
r = api("POST", "/warehouse/warehouse/new", STORE, json={"name": f"Склад {RUN}", "address": "Уфа"})
check("кладовщик создаёт склад", r.status_code == 201, r.text)
WID = r.json()["id"]
check("клиент не создаёт склад", api("POST", "/warehouse/warehouse/new", CLIENT_A, json={"name": "x"}).status_code == 403)
r = api("POST", "/warehouse/warehouse/receipt", STORE, json={"warehouse_id": WID, "product_id": PID, "qty": 100, "min_qty": 10})
check("приёмка 100 шт.", r.status_code == 201 and r.json()["qty"] == 100, r.text)
check("клиент не делает приёмку", api("POST", "/warehouse/warehouse/receipt", CLIENT_A, json={"warehouse_id": WID, "product_id": PID, "qty": 1}).status_code == 403)
r = api("GET", "/warehouse/warehouse/stock", STORE, params={"product_id": PID, "warehouse_id": WID})
check("остаток виден кладовщику", r.status_code == 200 and r.json()[0]["available"] == 100, r.text)
r = api("GET", "/warehouse/warehouse/stock", CLIENT_A, params={"product_id": PID})
check("клиент видит только доступное количество", r.status_code == 200 and "reserved_qty" not in r.json()[0], r.text)
r = api("POST", "/warehouse/warehouse/new", STORE, json={"name": f"Пустой {RUN}"})
EMPTY_WID = r.json()["id"]
r = api("GET", "/warehouse/warehouse/stock", STORE, params={"warehouse_id": EMPTY_WID})
check("NoWarehouseData для пустого склада", r.status_code == 404 and "NoWarehouseData" in detail_text(r), r.text)


def stock_row():
    rows = api("GET", "/warehouse/warehouse/stock", STORE, params={"product_id": PID, "warehouse_id": WID}).json()
    return rows[0]


def totals():
    """Суммарные остатки товара по всем складам (резерв может попасть на любой склад)."""
    rows = api("GET", "/warehouse/warehouse/stock", STORE, params={"product_id": PID}).json()
    return {"qty": sum(r["qty"] for r in rows), "reserved_qty": sum(r["reserved_qty"] for r in rows)}


# =====================================================================
section("2. Клиенты (UC-07)")
inn = ("77" + RUN + "00000")[:10]
r = api("POST", "/clients/client/new", CLIENT_A, json={"inn": inn, "kpp": "770101001", "name": "ООО Альфа", "legal_address": "Уфа, ул. Ленина, 1"})
check("клиент создаёт карточку", r.status_code == 201 and r.json()["user_id"] == CLIENT_A_ID, r.text)
CARD_A = r.json()["id"]
check("статус новой карточки new", r.json()["status"] == "new")
check("дубликат ИНН даёт 409", api("POST", "/clients/client/new", CLIENT_B, json={"inn": inn, "name": "Копия", "legal_address": "x"}).status_code == 409)
check("неверный ИНН даёт 422", api("POST", "/clients/client/new", CLIENT_B, json={"inn": "123", "name": "Плохой", "legal_address": "x"}).status_code == 422)
check("клиент не меняет кредитный лимит", api("PUT", f"/clients/client/edit/{CARD_A}", CLIENT_A, json={"credit_limit": 999999}).status_code == 403)
limit = round(PRICE * QTY * 1.5, 2)
r = api("PUT", f"/clients/client/edit/{CARD_A}", MANAGER, json={"credit_limit": limit, "manager_id": 1})
check("менеджер задаёт кредитный лимит", r.status_code == 200 and r.json()["credit_limit"] == limit, r.text)
r = api("PUT", f"/clients/client/status/{CARD_A}", MANAGER, json={"status": "active"})
check("менеджер активирует клиента", r.status_code == 200 and r.json()["status"] == "active", r.text)
r = api("POST", f"/clients/client/{CARD_A}/contacts", CLIENT_A, json={"full_name": "Иванов И.И.", "phone": "+7 900 000-00-00"})
check("клиент добавляет контакт", r.status_code == 201, r.text)
check("клиент B не видит карточку A", api("GET", f"/clients/client/{CARD_A}", CLIENT_B).status_code == 403)
check("клиент видит свою карточку (/me)", api("GET", "/clients/client/me", CLIENT_A).json()["id"] == CARD_A)

# =====================================================================
section("3. Заказ: проверки, кредитный лимит, резерв (UC-05)")
order_body = {"items": [{"product_id": PID, "qty": QTY}], "delivery_address": "Уфа, склад клиента"}
r = api("POST", "/orders/order/new", CLIENT_B, json=order_body)
check("без карточки клиента заказ не создаётся (409)", r.status_code == 409 and "ClientCardRequired" in detail_text(r), r.text)
if MIN_QTY > 1:
    r = api("POST", "/orders/order/new", CLIENT_A, json={"items": [{"product_id": PID, "qty": MIN_QTY - 1}]})
    check("партия меньше минимальной даёт 422", r.status_code == 422, r.text)
before = totals()
r = api("POST", "/orders/order/new", CLIENT_A, json=order_body)
check("заказ №1 создан", r.status_code == 201, r.text)
ORDER1 = r.json()
check("сумма заказа = цена × количество", abs(ORDER1["total_amount"] - PRICE * QTY) < 0.01)
check("статус pending", ORDER1["status"] == "pending")
after = totals()
check("резерв увеличился на количество заказа", after["reserved_qty"] == before["reserved_qty"] + QTY, str(after))
check("физический остаток не изменился", after["qty"] == before["qty"])

r = api("POST", "/orders/order/new", CLIENT_A, json=order_body)
check("превышение кредитного лимита даёт 409", r.status_code == 409 and "CreditLimitExceeded" in detail_text(r), r.text)
check("при отказе резерв не меняется", totals()["reserved_qty"] == after["reserved_qty"])

api("PUT", f"/clients/client/edit/{CARD_A}", MANAGER, json={"credit_limit": 0})
r = api("POST", "/orders/order/new", CLIENT_A, json={"items": [{"product_id": PID, "qty": 100000}]})
check("нехватка на складе даёт 409 InsufficientStock", r.status_code == 409 and "InsufficientStock" in detail_text(r), r.text)
check("при нехватке резерв не меняется", totals()["reserved_qty"] == after["reserved_qty"])
r = api("GET", "/orders/order/", CLIENT_A)
check("после отката в списке ровно один заказ", r.status_code == 200 and len(r.json()) == 1, r.text)

api("PUT", f"/clients/client/status/{CARD_A}", MANAGER, json={"status": "blocked"})
r = api("POST", "/orders/order/new", CLIENT_A, json=order_body)
check("заблокированный клиент не может заказывать (403)", r.status_code == 403, r.text)
api("PUT", f"/clients/client/status/{CARD_A}", MANAGER, json={"status": "active"})

# =====================================================================
section("4. Статусы, отмена, отгрузка (UC-06, UC-09)")
oid = ORDER1["id"]
check("клиент B не видит чужой заказ", api("GET", f"/orders/order/{oid}", CLIENT_B).status_code == 403)
check("клиент не меняет статус", api("PUT", f"/orders/order/edit/{oid}", CLIENT_A, json={"status": "confirmed"}).status_code == 403)
r = api("PUT", f"/orders/order/edit/{oid}", MANAGER, json={"status": "shipped"})
check("статус shipped вручную запрещён (409)", r.status_code == 409, r.text)
snap = totals()
r = api("POST", f"/warehouse/warehouse/ship/{oid}", STORE)
check("отгрузка неподтверждённого заказа отклонена (409)", r.status_code == 409, r.text)
check("при отказе отгрузка откатилась (остатки прежние)", totals() == snap)
r = api("PUT", f"/orders/order/edit/{oid}", MANAGER, json={"status": "confirmed"})
check("менеджер подтверждает заказ", r.status_code == 200 and r.json()["status"] == "confirmed", r.text)
check("клиент не отгружает", api("POST", f"/warehouse/warehouse/ship/{oid}", CLIENT_A).status_code == 403)
check("бухгалтер не отгружает", api("POST", f"/warehouse/warehouse/ship/{oid}", ACCT).status_code == 403)
before_ship = totals()
r = api("POST", f"/warehouse/warehouse/ship/{oid}", STORE)
check("кладовщик отгружает заказ", r.status_code == 201 and r.json()["shipment"]["number"].startswith("SHP-"), r.text)
after_ship = totals()
check("остаток списан", after_ship["qty"] == before_ship["qty"] - QTY, str(after_ship))
check("резерв снят", after_ship["reserved_qty"] == before_ship["reserved_qty"] - QTY)
r = api("GET", f"/orders/order/{oid}", CLIENT_A)
check("заказ получил статус shipped", r.json()["status"] == "shipped", r.text)
check("история статусов содержит shipped", [h["status"] for h in r.json()["history"]][-1] == "shipped")
check("повторная отгрузка невозможна (404)", api("POST", f"/warehouse/warehouse/ship/{oid}", STORE).status_code == 404)
check("отгруженный заказ нельзя отменить (409)", api("PUT", f"/orders/order/cancel/{oid}", CLIENT_A).status_code == 409)
r = api("GET", "/warehouse/warehouse/movements", STORE, params={"product_id": PID, "type": "shipment"})
check("движение «отгрузка» записано", r.status_code == 200 and any(m["qty"] == -QTY for m in r.json()), r.text)

r = api("POST", "/orders/order/new", CLIENT_A, json=order_body)
ORDER2 = r.json()
check("заказ №2 создан для проверки отмены", r.status_code == 201, r.text)
reserved_with_order2 = totals()["reserved_qty"]
r = api("PUT", f"/orders/order/cancel/{ORDER2['id']}", CLIENT_A)
check("клиент отменяет свой заказ", r.status_code == 200 and r.json()["status"] == "cancelled", r.text)
check("при отмене резерв снят", totals()["reserved_qty"] == reserved_with_order2 - QTY)
check("повторная отмена даёт 409", api("PUT", f"/orders/order/cancel/{ORDER2['id']}", CLIENT_A).status_code == 409)

# =====================================================================
section("5. Списание и инвентаризация (расширения UC-09)")
base = stock_row()
r = api("POST", "/warehouse/warehouse/writeoff", STORE, json={"warehouse_id": WID, "product_id": PID, "qty": 1, "reason": "повреждение при хранении"})
check("списание повреждённого товара", r.status_code == 201 and r.json()["qty"] == base["qty"] - 1, r.text)
r = api("POST", "/warehouse/warehouse/adjust", STORE, json={"warehouse_id": WID, "product_id": PID, "new_qty": base["qty"], "reason": "инвентаризация"})
check("корректировка по инвентаризации", r.status_code == 201 and r.json()["qty"] == base["qty"], r.text)
r = api("POST", "/warehouse/warehouse/writeoff", STORE, json={"warehouse_id": WID, "product_id": PID, "qty": 10**6, "reason": "тест"})
check("нельзя списать больше остатка (409)", r.status_code == 409)

# =====================================================================
section("6. Финансы: счёт и платежи (UC-10, UC-11)")
r = api("POST", "/finance/finance/invoice/new", ACCT, json={"order_id": ORDER2["id"]})
check("на отменённый заказ счёт не выставляется (409)", r.status_code == 409, r.text)
check("клиент не выставляет счёт", api("POST", "/finance/finance/invoice/new", CLIENT_A, json={"order_id": oid}).status_code == 403)
check("кладовщик не выставляет счёт", api("POST", "/finance/finance/invoice/new", STORE, json={"order_id": oid}).status_code == 403)
r = api("POST", "/finance/finance/invoice/new", ACCT, json={"order_id": oid, "vat_rate": 20, "due_days": 10})
check("бухгалтер выставляет счёт", r.status_code == 201, r.text)
INV = r.json()
net = round(PRICE * QTY, 2)
check("НДС и итог посчитаны", abs(INV["total"] - round(net * 1.2, 2)) < 0.02 and abs(INV["vat_amount"] - round(net * 0.2, 2)) < 0.02, str(INV))
check("реквизиты клиента подставлены", INV["client_inn"] == inn and INV["client_name"] == "ООО Альфа")
check("позиции счёта скопированы из заказа", len(INV["items"]) == 1)
check("дубликат счёта на заказ даёт 409", api("POST", "/finance/finance/invoice/new", ACCT, json={"order_id": oid}).status_code == 409)
check("до отправки клиент счёт не видит", api("GET", "/finance/finance/invoice/", CLIENT_A).json() == [])
r = api("POST", f"/finance/finance/invoice/send/{INV['id']}", ACCT)
check("счёт отправлен клиенту", r.status_code == 200 and r.json()["status"] == "sent", r.text)
r = api("GET", "/finance/finance/invoice/", CLIENT_A)
check("клиент видит свой счёт", r.status_code == 200 and len(r.json()) == 1, r.text)
check("клиент B не видит чужой счёт", api("GET", f"/finance/finance/invoice/{INV['id']}", CLIENT_B).status_code == 403)

total = INV["total"]
part = round(total / 2, 2)
r = api("POST", "/finance/finance/payment/new", ACCT, json={"client_id": CLIENT_A_ID, "amount": part, "method": "bank_transfer", "external_doc_number": f"PP-{RUN}-1"})
check("частичная оплата принята", r.status_code == 201 and r.json()["unallocated"] == 0, r.text)
r = api("GET", f"/finance/finance/invoice/{INV['id']}", ACCT)
check("счёт partially_paid", r.json()["status"] == "partially_paid", r.text)
r = api("POST", "/finance/finance/payment/new", ACCT, json={"client_id": CLIENT_A_ID, "amount": part, "external_doc_number": f"PP-{RUN}-1"})
check("дубликат платёжного документа даёт 409", r.status_code == 409, r.text)
r = api("POST", "/finance/finance/payment/new", ACCT, json={"client_id": CLIENT_A_ID, "amount": round(total - part + 100, 2), "method": "card", "external_doc_number": f"PP-{RUN}-2"})
check("переплата принята, 100 остаются авансом", r.status_code == 201 and abs(r.json()["unallocated"] - 100) < 0.01, r.text)
r = api("GET", f"/finance/finance/invoice/{INV['id']}", ACCT)
check("счёт полностью оплачен", r.json()["status"] == "paid", r.text)
r = api("GET", f"/finance/finance/balance/{CLIENT_A_ID}", CLIENT_A)
check("баланс: долга нет, аванс 100", r.status_code == 200 and r.json()["debt"] == 0 and abs(r.json()["advance"] - 100) < 0.01, r.text)
check("клиент B не видит баланс A", api("GET", f"/finance/finance/balance/{CLIENT_A_ID}", CLIENT_B).status_code == 403)
check("клиент не вносит платежи", api("POST", "/finance/finance/payment/new", CLIENT_A, json={"client_id": CLIENT_A_ID, "amount": 1}).status_code == 403)
check("нельзя отменить оплаченный счёт (409)", api("PUT", f"/finance/finance/invoice/cancel/{INV['id']}", ACCT).status_code == 409)

# =====================================================================
section("7. Отчётность (UC-12)")
today = time.strftime("%Y-%m-%d")
r = api("POST", "/reporting/reporting/sync", ADMIN_TOKEN, json={})
check("загрузка метрик из заказов", r.status_code == 200 and r.json()["rows_upserted"] >= 1, r.text)
r2 = api("POST", "/reporting/reporting/sync", ADMIN_TOKEN, json={})
check("повторная загрузка не плодит дубли", r2.status_code == 200 and r2.json()["rows_upserted"] == r.json()["rows_upserted"])
period = {"date_from": "2000-01-01", "date_to": "2100-01-01"}
for group in ("month", "product", "client"):
    r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={**period, "group_by": group})
    check(f"директор строит отчёт по группировке «{group}»", r.status_code == 200 and r.json()["rows"], r.text)
r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={**period, "group_by": "client"})
mine = [row for row in r.json()["rows"] if row["key"] == str(CLIENT_A_ID)]
check("выручка клиента A = сумма отгруженного заказа", mine and abs(mine[0]["revenue"] - net) < 0.01, str(mine))
check("отменённый заказ в отчёт не попал", mine and mine[0]["orders"] == 1, str(mine))
r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={"date_from": "1999-01-01", "date_to": "1999-12-31"})
check("пустой период даёт NoData (404)", r.status_code == 404 and "NoData" in detail_text(r), r.text)
r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={"date_from": "2026-02-01", "date_to": "2026-01-01"})
check("перевёрнутый период даёт 422", r.status_code == 422, r.text)
r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={**period, "group_by": "weekday"})
check("неверная группировка даёт 422", r.status_code == 422, r.text)
r = api("POST", "/reporting/reporting/sales", DIRECTOR, json={**period, "group_by": "month", "save": True, "name": f"Тест {RUN}"})
check("параметры отчёта сохраняются", r.status_code == 200 and r.json().get("saved_report_id"), r.text)
SAVED = r.json().get("saved_report_id")
r = api("GET", "/reporting/reporting/saved", DIRECTOR)
check("сохранённый отчёт в списке", r.status_code == 200 and any(x["id"] == SAVED for x in r.json()), r.text)
r = api("POST", f"/reporting/reporting/saved/{SAVED}/run", DIRECTOR)
check("сохранённый отчёт запускается повторно", r.status_code == 200 and r.json()["rows"], r.text)
check("клиент не видит отчёты", api("POST", "/reporting/reporting/sales", CLIENT_A, json={**period}).status_code == 403)
check("кладовщик не видит отчёты", api("POST", "/reporting/reporting/sales", STORE, json={**period}).status_code == 403)

# =====================================================================
section("8. Удаление (admin)")
check("менеджер не удаляет заказ", api("DELETE", f"/orders/order/delete/{ORDER2['id']}", MANAGER).status_code == 403)
check("админ удаляет заказ", api("DELETE", f"/orders/order/delete/{ORDER2['id']}", ADMIN_TOKEN).status_code == 204)

print(f"\nИтого: {passed} OK, {failed} FAIL")
sys.exit(1 if failed else 0)
