"""Веб-интерфейс (Jinja2). Все действия выполняются через API gateway, как и в Swagger."""
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import api
from api import ApiError, has

app = FastAPI(title="Веб-интерфейс оптовой фирмы", version="1.0.0")
templates = Jinja2Templates(directory="templates")

ROLE_RU = {
    "client": "Клиент", "manager": "Менеджер", "admin": "Администратор",
    "storekeeper": "Кладовщик", "accountant": "Бухгалтер", "director": "Директор",
}
STATUS_RU = {
    "pending": "Ожидает подтверждения", "confirmed": "Подтверждён", "shipped": "Отгружен",
    "cancelled": "Отменён", "issued": "Выставлен", "sent": "Отправлен клиенту",
    "partially_paid": "Оплачен частично", "paid": "Оплачен", "new": "Новый",
    "active": "Активный", "blocked": "Заблокирован",
}
METHOD_RU = {"bank_transfer": "Банковский перевод", "cash": "Наличные", "card": "Карта"}
GROUP_RU = {"month": "по месяцам", "product": "по товарам", "client": "по клиентам"}


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _dt(value: Any) -> str:
    return str(value).replace("T", " ")[:16] if value else "—"


templates.env.filters["st"] = lambda v: STATUS_RU.get(v, v)
templates.env.filters["role_ru"] = lambda v: ROLE_RU.get(v, v)
templates.env.filters["method_ru"] = lambda v: METHOD_RU.get(v, v)
templates.env.filters["group_ru"] = lambda v: GROUP_RU.get(v, v)
templates.env.filters["money"] = _money
templates.env.filters["dt"] = _dt
templates.env.globals["has"] = has
templates.env.globals["ROLE_RU"] = ROLE_RU
templates.env.globals["ALL_ROLES"] = api.ROLES

FIELD_LABELS = {
    "email": "Email", "password": "Пароль", "full_name": "ФИО", "name": "Название / ФИО",
    "inn": "ИНН", "kpp": "КПП", "phone": "Телефон", "username": "Логин", "company": "Организация",
    "company_name": "Организация", "address": "Адрес", "legal_address": "Юридический адрес",
}


class LoginRequired(Exception):
    pass


class Form:
    def __init__(self, raw: str):
        self.data = parse_qs(raw, keep_blank_values=True)

    def get(self, key: str, default: str = "") -> str:
        values = self.data.get(key)
        return values[0].strip() if values else default

    def getlist(self, key: str) -> List[str]:
        return [value.strip() for value in self.data.get(key, [])]

    def to_int(self, key: str) -> Optional[int]:
        value = self.get(key)
        return int(value) if value else None

    def to_float(self, key: str) -> Optional[float]:
        value = self.get(key).replace(",", ".")
        return float(value) if value else None


async def read_form(request: Request) -> Form:
    return Form((await request.body()).decode("utf-8"))


def need_user(request: Request) -> dict:
    token = request.cookies.get("token")
    user = api.user_from_token(token) if token else None
    if user is None:
        raise LoginRequired()
    return user


def A(user: dict, method: str, path: str, **kwargs: Any) -> Any:
    try:
        return api.call(method, path, user["token"], **kwargs)
    except ApiError as exc:
        if exc.status == 401:
            raise LoginRequired()
        raise


def soft(user: dict, method: str, path: str, default: Any = None, **kwargs: Any) -> Any:
    """Необязательные данные: при ошибке возвращаем значение по умолчанию."""
    try:
        return A(user, method, path, **kwargs)
    except ApiError:
        return default


def redirect(url: str, msg: Optional[str] = None, err: Optional[str] = None) -> RedirectResponse:
    extra = {}
    if msg:
        extra["msg"] = msg
    if err:
        extra["err"] = err
    if extra:
        url += ("&" if "?" in url else "?") + urlencode(extra)
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, user: Optional[dict], **context: Any) -> HTMLResponse:
    context.update(
        user=user,
        msg=context.get("msg") or request.query_params.get("msg"),
        err=context.get("err") or request.query_params.get("err"),
    )
    return templates.TemplateResponse(request, name, context)


def run(fn, back: str, ok: str):
    """Выполняет действие; функция может вернуть адрес для перехода."""
    try:
        result = fn()
    except ApiError as exc:
        return redirect(back, err=exc.detail)
    return redirect(result if isinstance(result, str) else back, msg=ok)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("token")
    return response


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError):
    token = request.cookies.get("token")
    user = api.user_from_token(token) if token else None
    page = templates.TemplateResponse(
        request, "error.html", {"user": user, "msg": None, "err": None, "status": exc.status, "detail": exc.detail},
        status_code=200,
    )
    return page


def product_names(user: dict) -> Dict[int, str]:
    try:
        return {p["id"]: p.get("name", f"Товар {p['id']}") for p in api.products(user["token"])}
    except ApiError:
        return {}


# ======================= вход и регистрация =======================

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", None)


@app.post("/login")
async def login_submit(request: Request):
    form = await read_form(request)
    try:
        token = api.login(form.get("email"), form.get("password"))
    except ApiError as exc:
        return render(request, "login.html", None, err=exc.detail, email=form.get("email"))
    response = redirect("/")
    response.set_cookie("token", token, httponly=True, samesite="lax", max_age=86400)
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    try:
        fields = api.register_fields()
    except ApiError as exc:
        return render(request, "register.html", None, fields=[], err=exc.detail, labels=FIELD_LABELS)
    return render(request, "register.html", None, fields=fields, labels=FIELD_LABELS, values={})


@app.post("/register")
async def register_submit(request: Request):
    form = await read_form(request)
    values = {key: form.get(key) for key in form.data}
    try:
        api.register(values)
    except ApiError as exc:
        fields = api.register_fields()
        return render(request, "register.html", None, fields=fields, labels=FIELD_LABELS, values=values, err=exc.detail)
    return redirect("/login", msg="Регистрация выполнена, теперь можно войти")


@app.get("/logout")
def logout():
    response = redirect("/login")
    response.delete_cookie("token")
    return response


# ======================= главная и каталог =======================

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = need_user(request)
    return render(request, "home.html", user)


@app.get("/catalog", response_class=HTMLResponse)
def catalog_page(request: Request):
    user = need_user(request)
    items = api.products(user["token"])
    stock = soft(user, "GET", "/warehouse/warehouse/stock", [])
    available: Dict[int, int] = {}
    for row in stock or []:
        available[row["product_id"]] = available.get(row["product_id"], 0) + int(row.get("available", 0))
    return render(request, "catalog.html", user, items=items, available=available)


# ======================= заказы =======================

@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request):
    user = need_user(request)
    status = request.query_params.get("status", "")
    orders = A(user, "GET", "/orders/order/", params={"status": status})
    return render(request, "orders.html", user, orders=orders, status=status)


@app.get("/orders/new", response_class=HTMLResponse)
def order_new_page(request: Request):
    user = need_user(request)
    items = api.products(user["token"])
    return render(request, "order_new.html", user, products=items,
                  selected=request.query_params.get("product_id", ""))


@app.post("/orders/new")
async def order_new_submit(request: Request):
    user = need_user(request)
    form = await read_form(request)
    lines = []
    for product_id, qty in zip(form.getlist("product_id"), form.getlist("qty")):
        if product_id and qty:
            lines.append({"product_id": int(product_id), "qty": int(qty)})
    if not lines:
        return redirect("/orders/new", err="Добавьте хотя бы одну позицию")
    payload: Dict[str, Any] = {
        "items": lines,
        "delivery_address": form.get("delivery_address") or None,
        "comment": form.get("comment") or None,
    }
    if form.get("client_id") and has(user, "manager"):
        payload["client_id"] = int(form.get("client_id"))
    return run(
        lambda: f"/orders/{A(user, 'POST', '/orders/order/new', json=payload)['id']}",
        "/orders/new", "Заказ оформлен, товар зарезервирован на складе",
    )


@app.get("/orders/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, order_id: int):
    user = need_user(request)
    order = A(user, "GET", f"/orders/order/{order_id}")
    invoices = soft(user, "GET", "/finance/finance/invoice/", [], params={"order_id": order_id})
    shipments = []
    if has(user, "storekeeper", "manager", "accountant", "director"):
        shipments = soft(user, "GET", f"/warehouse/warehouse/shipments/{order_id}", [])
    return render(request, "order.html", user, o=order, invoices=invoices or [],
                  shipments=shipments or [], names=product_names(user))


@app.post("/orders/{order_id}/action")
async def order_action(request: Request, order_id: int):
    user = need_user(request)
    form = await read_form(request)
    action = form.get("action")
    back = f"/orders/{order_id}"
    if action == "confirm":
        return run(lambda: A(user, "PUT", f"/orders/order/edit/{order_id}", json={"status": "confirmed"}), back, "Заказ подтверждён")
    if action == "pending":
        return run(lambda: A(user, "PUT", f"/orders/order/edit/{order_id}", json={"status": "pending"}), back, "Заказ возвращён в ожидание")
    if action == "update":
        payload = {"delivery_address": form.get("delivery_address"), "comment": form.get("comment")}
        return run(lambda: A(user, "PUT", f"/orders/order/edit/{order_id}", json=payload), back, "Данные заказа сохранены")
    if action == "cancel":
        return run(lambda: A(user, "PUT", f"/orders/order/cancel/{order_id}"), back, "Заказ отменён, резерв снят")
    if action == "ship":
        return run(lambda: A(user, "POST", f"/warehouse/warehouse/ship/{order_id}"), back, "Заказ отгружен, накладная создана")
    if action == "invoice":
        payload = {"order_id": order_id, "vat_rate": form.to_float("vat_rate") or 20.0, "due_days": form.to_int("due_days") or 14}
        return run(lambda: f"/finance/invoices/{A(user, 'POST', '/finance/finance/invoice/new', json=payload)['id']}",
                   back, "Счёт выставлен")
    if action == "delete":
        return run(lambda: (A(user, "DELETE", f"/orders/order/delete/{order_id}"), "/orders")[1], back, "Заказ удалён")
    return redirect(back, err="Неизвестное действие")


# ======================= клиенты =======================

@app.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request):
    user = need_user(request)
    if not has(user, "manager", "accountant", "director"):
        try:
            card = A(user, "GET", "/clients/client/me")
        except ApiError as exc:
            if exc.status == 404:
                return redirect("/clients/new", msg="Создайте карточку клиента, чтобы оформлять заказы")
            raise
        return redirect(f"/clients/{card['id']}")
    status = request.query_params.get("status", "")
    clients = A(user, "GET", "/clients/client/", params={"status": status})
    return render(request, "clients.html", user, clients=clients, status=status)


@app.get("/clients/new", response_class=HTMLResponse)
def client_new_page(request: Request):
    user = need_user(request)
    return render(request, "client_new.html", user)


@app.post("/clients/new")
async def client_new_submit(request: Request):
    user = need_user(request)
    form = await read_form(request)
    payload: Dict[str, Any] = {
        "inn": form.get("inn"), "kpp": form.get("kpp") or None,
        "name": form.get("name"), "legal_address": form.get("legal_address"),
    }
    if has(user, "manager"):
        if form.get("user_id"):
            payload["user_id"] = form.to_int("user_id")
        if form.get("manager_id"):
            payload["manager_id"] = form.to_int("manager_id")
        if form.get("credit_limit"):
            payload["credit_limit"] = form.to_float("credit_limit")
    return run(lambda: f"/clients/{A(user, 'POST', '/clients/client/new', json=payload)['id']}",
               "/clients/new", "Карточка клиента создана")


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_page(request: Request, client_id: int):
    user = need_user(request)
    client = A(user, "GET", f"/clients/client/{client_id}")
    contacts = soft(user, "GET", f"/clients/client/{client_id}/contacts", [])
    return render(request, "client.html", user, c=client, contacts=contacts or [])


@app.post("/clients/{client_id}/action")
async def client_action(request: Request, client_id: int):
    user = need_user(request)
    form = await read_form(request)
    action = form.get("action")
    back = f"/clients/{client_id}"
    if action == "edit":
        payload: Dict[str, Any] = {}
        if has(user, "manager"):
            for key in ("name", "kpp", "legal_address"):
                if form.get(key):
                    payload[key] = form.get(key)
            if form.get("manager_id"):
                payload["manager_id"] = form.to_int("manager_id")
            if form.get("credit_limit"):
                payload["credit_limit"] = form.to_float("credit_limit")
        elif form.get("legal_address"):
            payload["legal_address"] = form.get("legal_address")
        return run(lambda: A(user, "PUT", f"/clients/client/edit/{client_id}", json=payload), back, "Данные клиента сохранены")
    if action == "status":
        return run(lambda: A(user, "PUT", f"/clients/client/status/{client_id}", json={"status": form.get("status")}),
                   back, "Статус клиента изменён")
    if action == "add_contact":
        payload = {"full_name": form.get("full_name"), "position": form.get("position") or None,
                   "phone": form.get("phone") or None, "email": form.get("email") or None}
        return run(lambda: A(user, "POST", f"/clients/client/{client_id}/contacts", json=payload), back, "Контакт добавлен")
    if action == "del_contact":
        return run(lambda: A(user, "DELETE", f"/clients/client/contact/{form.get('contact_id')}"), back, "Контакт удалён")
    if action == "delete":
        return run(lambda: (A(user, "DELETE", f"/clients/client/delete/{client_id}"), "/clients")[1], back, "Карточка удалена")
    return redirect(back, err="Неизвестное действие")


# ======================= склад =======================

@app.get("/warehouse", response_class=HTMLResponse)
def warehouse_page(request: Request):
    user = need_user(request)
    product_id = request.query_params.get("product_id", "")
    warehouse_id = request.query_params.get("warehouse_id", "")
    stock = []
    stock_error = None
    try:
        stock = A(user, "GET", "/warehouse/warehouse/stock",
                  params={"product_id": product_id, "warehouse_id": warehouse_id})
    except ApiError as exc:
        stock_error = exc.detail
    warehouses = soft(user, "GET", "/warehouse/warehouse/", [])
    movements = []
    if has(user, "storekeeper", "manager", "director", "accountant"):
        movements = (soft(user, "GET", "/warehouse/warehouse/movements", []) or [])[-30:][::-1]
    try:
        items = api.products(user["token"])
    except ApiError:
        items = []
    return render(request, "warehouse.html", user, stock=stock, warehouses=warehouses or [],
                  movements=movements, products=items, names={p["id"]: p.get("name") for p in items},
                  wnames={w["id"]: w["name"] for w in (warehouses or [])},
                  product_id=product_id, warehouse_id=warehouse_id, err=stock_error)


@app.post("/warehouse/action")
async def warehouse_action(request: Request):
    user = need_user(request)
    form = await read_form(request)
    action = form.get("action")
    back = "/warehouse"
    if action == "create":
        payload = {"name": form.get("name"), "address": form.get("address") or None}
        return run(lambda: A(user, "POST", "/warehouse/warehouse/new", json=payload), back, "Склад создан")
    base = {"warehouse_id": form.to_int("warehouse_id"), "product_id": form.to_int("product_id")}
    if action == "receipt":
        payload = {**base, "qty": form.to_int("qty")}
        if form.get("min_qty"):
            payload["min_qty"] = form.to_int("min_qty")
        return run(lambda: A(user, "POST", "/warehouse/warehouse/receipt", json=payload), back, "Товар принят на склад")
    if action == "writeoff":
        payload = {**base, "qty": form.to_int("qty"), "reason": form.get("reason")}
        return run(lambda: A(user, "POST", "/warehouse/warehouse/writeoff", json=payload), back, "Товар списан")
    if action == "adjust":
        payload = {**base, "new_qty": form.to_int("new_qty"), "reason": form.get("reason")}
        return run(lambda: A(user, "POST", "/warehouse/warehouse/adjust", json=payload), back, "Остаток скорректирован")
    return redirect(back, err="Неизвестное действие")


# ======================= финансы =======================

def _client_options(user: dict) -> List[dict]:
    cards = soft(user, "GET", "/clients/client/", []) or []
    return [c for c in cards if c.get("user_id") is not None]


@app.get("/finance", response_class=HTMLResponse)
def finance_page(request: Request):
    user = need_user(request)
    status = request.query_params.get("status", "")
    invoices = A(user, "GET", "/finance/finance/invoice/", params={"status": status})
    payments = A(user, "GET", "/finance/finance/payment/")
    staff = has(user, "accountant", "manager", "director")
    options = _client_options(user) if staff else []
    client_id = request.query_params.get("client_id", "")
    balance = None
    if not staff:
        client_id = str(user["id"])
    if client_id:
        balance = soft(user, "GET", f"/finance/finance/balance/{client_id}")
    return render(request, "finance.html", user, invoices=invoices, payments=payments, status=status,
                  options=options, client_id=client_id, balance=balance, today=date.today().isoformat(),
                  cnames={c["user_id"]: c["name"] for c in options})


@app.post("/finance/payment")
async def payment_submit(request: Request):
    user = need_user(request)
    form = await read_form(request)
    payload = {
        "client_id": form.to_int("client_id"), "amount": form.to_float("amount"),
        "method": form.get("method") or "bank_transfer",
        "external_doc_number": form.get("external_doc_number") or None,
        "payment_date": form.get("payment_date") or None,
    }
    try:
        result = A(user, "POST", "/finance/finance/payment/new", json=payload)
    except ApiError as exc:
        return redirect("/finance", err=exc.detail)
    note = f"Платёж внесён. Не распределено (аванс): {_money(result['unallocated'])}"
    return redirect(f"/finance?client_id={payload['client_id']}", msg=note)


@app.get("/finance/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_page(request: Request, invoice_id: int):
    user = need_user(request)
    invoice = A(user, "GET", f"/finance/finance/invoice/{invoice_id}")
    return render(request, "invoice.html", user, inv=invoice)


@app.post("/finance/invoices/{invoice_id}/action")
async def invoice_action(request: Request, invoice_id: int):
    user = need_user(request)
    form = await read_form(request)
    back = f"/finance/invoices/{invoice_id}"
    if form.get("action") == "send":
        return run(lambda: A(user, "POST", f"/finance/finance/invoice/send/{invoice_id}"), back, "Счёт отправлен клиенту")
    if form.get("action") == "cancel":
        return run(lambda: A(user, "PUT", f"/finance/finance/invoice/cancel/{invoice_id}"), back, "Счёт отменён")
    return redirect(back, err="Неизвестное действие")


# ======================= отчётность =======================

def _report_defaults() -> Dict[str, str]:
    today = date.today()
    return {"date_from": date(today.year, 1, 1).isoformat(), "date_to": today.isoformat(), "group_by": "month"}


@app.get("/reporting", response_class=HTMLResponse)
def reporting_page(request: Request):
    user = need_user(request)
    saved = soft(user, "GET", "/reporting/reporting/saved", [])
    return render(request, "reporting.html", user, saved=saved or [], form=_report_defaults(), result=None)


@app.post("/reporting/run")
async def reporting_run(request: Request):
    user = need_user(request)
    form = await read_form(request)
    values = {"date_from": form.get("date_from"), "date_to": form.get("date_to"), "group_by": form.get("group_by") or "month"}
    payload = {**values, "save": form.get("save") == "on", "name": form.get("name") or None}
    saved = soft(user, "GET", "/reporting/reporting/saved", [])
    try:
        result = A(user, "POST", "/reporting/reporting/sales", json=payload)
    except ApiError as exc:
        text = "Нет данных за выбранный период" if "NoData" in exc.detail else exc.detail
        return render(request, "reporting.html", user, saved=saved or [], form=values, result=None, err=text)
    note = None
    if result.get("saved_report_id"):
        note = "Параметры отчёта сохранены"
        saved = soft(user, "GET", "/reporting/reporting/saved", [])
    return render(request, "reporting.html", user, saved=saved or [], form=values, result=result, msg=note)


@app.post("/reporting/sync")
async def reporting_sync(request: Request):
    user = need_user(request)

    try:
        result = A(user, "POST", "/reporting/reporting/sync", json={})
    except ApiError as exc:
        return redirect("/reporting", err=exc.detail)
    return redirect("/reporting", msg=f"Загружено заказов: {result['orders_processed']}, строк метрик: {result['rows_upserted']}")


@app.post("/reporting/saved/{report_id}/{action}")
async def reporting_saved(request: Request, report_id: int, action: str):
    user = need_user(request)
    if action == "delete":
        return run(lambda: A(user, "DELETE", f"/reporting/reporting/saved/{report_id}"), "/reporting", "Сохранённый отчёт удалён")
    saved = soft(user, "GET", "/reporting/reporting/saved", [])
    params = next((s["parameters"] for s in (saved or []) if s["id"] == report_id), None) or _report_defaults()
    try:
        result = A(user, "POST", f"/reporting/reporting/saved/{report_id}/run")
    except ApiError as exc:
        text = "Нет данных за выбранный период" if "NoData" in exc.detail else exc.detail
        return render(request, "reporting.html", user, saved=saved or [], form=params, result=None, err=text)
    return render(request, "reporting.html", user, saved=saved or [], form=params, result=result)


# ======================= пользователи (admin) =======================

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = need_user(request)
    users = api.list_users(user["token"])
    return render(request, "users.html", user, users=users)


@app.post("/users/role")
async def users_role(request: Request):
    user = need_user(request)
    form = await read_form(request)
    return run(lambda: api.set_role(user["token"], form.to_int("user_id"), form.get("role")), "/users",
               "Роль изменена: пользователю нужно войти заново, чтобы она применилась")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
