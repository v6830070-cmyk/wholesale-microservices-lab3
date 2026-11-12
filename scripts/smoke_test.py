import sys
import time

import httpx

BASE_URL = "http://127.0.0.1:8000"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Admin12345"
PASSWORD = "Password123"

client = httpx.Client(base_url=BASE_URL, timeout=10.0)
results = []


def record(name: str, ok: bool, details: str = "") -> None:
    results.append(ok)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name} {details}")


def check(name: str, response: httpx.Response, expected: int) -> httpx.Response:
    record(name, response.status_code == expected,
           f"(ожидали {expected}, получили {response.status_code})")
    return response


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register(email: str, company: str, inn: str = "1234567890") -> httpx.Response:
    return client.post("/iam/auth/register", json={
        "email": email, "password": PASSWORD,
        "company_name": company, "inn": inn})


def login(email: str, password: str = PASSWORD) -> httpx.Response:
    return client.post("/iam/auth/login", json={"email": email, "password": password})


def main() -> None:
    suffix = str(int(time.time()))

    # --- Gateway и сервисы ---
    try:
        health = check("Gateway отвечает", client.get("/health"), 200)
    except httpx.RequestError:
        print("Gateway недоступен. Запустите все четыре сервиса.")
        sys.exit(1)
    record("Все сервисы запущены", all(v == "up" for v in health.json().values()),
           str(health.json()))
    check("Запрос без токена отклонён", client.get("/catalog/product/"), 401)
    check("Неверный токен отклонён",
          client.get("/catalog/product/", headers=bearer("bad.token.value")), 401)

    # --- IAM: регистрация и вход ---
    admin_token = check("Вход администратора",
                        login(ADMIN_EMAIL, ADMIN_PASSWORD), 200).json()["access_token"]

    client1_email = f"client1_{suffix}@example.com"
    client1_id = check("Регистрация клиента 1",
                       register(client1_email, "ООО Ромашка"), 201).json()["id"]
    check("Повторный email отклонён",
          register(client1_email, "ООО Ромашка"), 409)
    check("Неверный ИНН отклонён",
          register(f"bad_{suffix}@example.com", "ООО Тест", inn="123"), 400)
    check("Неверный пароль отклонён", login(client1_email, "wrong-password"), 401)
    client1_token = check("Вход клиента 1", login(client1_email), 200).json()["access_token"]

    manager_email = f"manager_{suffix}@example.com"
    manager_id = check("Регистрация будущего менеджера",
                       register(manager_email, "Оптовая фирма"), 201).json()["id"]
    check("Клиент не может назначать роли",
          client.put(f"/iam/users/role/{manager_id}", json={"role": "manager"},
                     headers=bearer(client1_token)), 403)
    check("Администратор назначает роль manager",
          client.put(f"/iam/users/role/{manager_id}", json={"role": "manager"},
                     headers=bearer(admin_token)), 200)
    manager_token = check("Вход менеджера", login(manager_email), 200).json()["access_token"]
    me = check("Профиль менеджера", client.get("/iam/auth/me",
                                               headers=bearer(manager_token)), 200)
    record("Роль менеджера в профиле", me.json().get("role") == "manager")

    # --- Каталог: права доступа ---
    unit_id = check("Менеджер создаёт единицу измерения",
                    client.post("/catalog/unit/new",
                                json={"code": f"u{suffix[-6:]}", "name": "Штука"},
                                headers=bearer(manager_token)), 201).json()["id"]
    category_id = check("Менеджер создаёт категорию",
                        client.post("/catalog/category/new",
                                    json={"name": "Бакалея", "slug": f"grocery-{suffix}"},
                                    headers=bearer(manager_token)), 201).json()["id"]
    product_body = {"sku": f"SUGAR-{suffix}", "name": "Сахар 50 кг",
                    "category_id": category_id, "unit_id": unit_id,
                    "weight_kg": 50, "cost_price": 2800,
                    "base_price": 3200, "min_qty": 10}
    check("Клиент не может создать товар",
          client.post("/catalog/product/new", json=product_body,
                      headers=bearer(client1_token)), 403)
    product_id = check("Менеджер создаёт товар",
                       client.post("/catalog/product/new", json=product_body,
                                   headers=bearer(manager_token)), 201).json()["id"]
    check("Клиент просматривает каталог",
          client.get("/catalog/product/", headers=bearer(client1_token)), 200)

    # --- Заказы ---
    order_body = {"delivery_address": "г. Уфа, ул. Ленина, 1",
                  "items": [{"product_id": product_id, "qty": 20}]}
    check("Количество ниже min_qty отклонено",
          client.post("/orders/order/new",
                      json={"delivery_address": "г. Уфа, ул. Ленина, 1",
                            "items": [{"product_id": product_id, "qty": 5}]},
                      headers=bearer(client1_token)), 400)
    created = check("Клиент оформляет заказ без client_id",
                    client.post("/orders/order/new", json=order_body,
                                headers=bearer(client1_token)), 201).json()
    order_id = created["id"]
    record("Сумма заказа 20 x 3200 = 64000", created["total_amount"] == 64000.0,
           f"(получили {created['total_amount']})")

    listing = check("Клиент видит список заказов",
                    client.get("/orders/order/", headers=bearer(client1_token)), 200).json()
    record("В списке только заказы клиента 1",
           all(o["client_id"] == client1_id for o in listing))

    client2_email = f"client2_{suffix}@example.com"
    client2_id = check("Регистрация клиента 2",
                       register(client2_email, "ИП Петров", inn="123456789012"), 201).json()["id"]
    client2_token = check("Вход клиента 2", login(client2_email), 200).json()["access_token"]
    check("Клиент 2 не видит чужой заказ",
          client.get(f"/orders/order/{order_id}", headers=bearer(client2_token)), 403)
    record("В списке клиента 2 нет чужих заказов",
           all(o["id"] != order_id for o in client.get(
               "/orders/order/", headers=bearer(client2_token)).json()))

    check("Менеджер без client_id получает 400",
          client.post("/orders/order/new", json=order_body,
                      headers=bearer(manager_token)), 400)
    check("Менеджер оформляет заказ для клиента 1",
          client.post("/orders/order/new",
                      json={**order_body, "client_id": client1_id},
                      headers=bearer(manager_token)), 201)

    check("Клиент не может менять статус",
          client.put(f"/orders/order/edit/{order_id}", json={"status": "confirmed"},
                     headers=bearer(client1_token)), 403)
    check("Менеджер подтверждает заказ",
          client.put(f"/orders/order/edit/{order_id}", json={"status": "confirmed"},
                     headers=bearer(manager_token)), 200)
    details = check("Клиент читает свой заказ",
                    client.get(f"/orders/order/{order_id}",
                               headers=bearer(client1_token)), 200).json()
    last = details["history"][-1]
    record("В истории статус confirmed и changed_by = id менеджера",
           last["status"] == "confirmed" and last["changed_by"] == manager_id,
           f"(получили {last['status']}, {last['changed_by']})")

    second_id = check("Клиент оформляет второй заказ",
                      client.post("/orders/order/new", json=order_body,
                                  headers=bearer(client1_token)), 201).json()["id"]
    check("Клиент 2 не может отменить чужой заказ",
          client.put(f"/orders/order/cancel/{second_id}",
                     headers=bearer(client2_token)), 403)
    check("Клиент отменяет свой заказ",
          client.put(f"/orders/order/cancel/{second_id}",
                     headers=bearer(client1_token)), 200)
    check("Повторная отмена отклонена",
          client.put(f"/orders/order/cancel/{second_id}",
                     headers=bearer(client1_token)), 409)

    check("Менеджер не может удалить заказ",
          client.delete(f"/orders/order/delete/{order_id}",
                        headers=bearer(manager_token)), 403)
    check("Администратор удаляет заказ",
          client.delete(f"/orders/order/delete/{order_id}",
                        headers=bearer(admin_token)), 200)
    check("Удалённый заказ не найден",
          client.get(f"/orders/order/{order_id}", headers=bearer(admin_token)), 404)

    # --- Управление пользователями ---
    check("Администратор видит список пользователей",
          client.get("/iam/users/", headers=bearer(admin_token)), 200)
    check("Клиент не видит список пользователей",
          client.get("/iam/users/", headers=bearer(client1_token)), 403)
    check("Администратор блокирует клиента 2",
          client.put(f"/iam/users/status/{client2_id}", json={"status": "blocked"},
                     headers=bearer(admin_token)), 200)
    check("Заблокированный клиент не может войти", login(client2_email), 403)

    passed = sum(results)
    print(f"\nИтого: {passed} из {len(results)} проверок пройдено")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()