from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database.connection import get_session
from logic import CLIENT_STATUSES, validate_credit_limit, validate_inn, validate_kpp
from models.clients import Client, Contact
from security import CurrentUser, get_current_user, has_role, require_roles

router = APIRouter(prefix="/client", tags=["Клиенты"])

STAFF = ("manager", "admin", "accountant", "director")


class ClientCreate(BaseModel):
    inn: str
    kpp: Optional[str] = None
    name: str = Field(min_length=1, max_length=255)
    legal_address: str = Field(min_length=1, max_length=500)
    user_id: Optional[int] = None
    manager_id: Optional[int] = None
    credit_limit: float = 0.0


class ClientEdit(BaseModel):
    kpp: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    legal_address: Optional[str] = Field(default=None, min_length=1, max_length=500)
    manager_id: Optional[int] = None
    credit_limit: Optional[float] = None


class StatusChange(BaseModel):
    status: str


class ContactCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    position: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


def _get_client(session: Session, client_id: int) -> Client:
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Клиент не найден")
    return client


def _can_access(user: CurrentUser, client: Client) -> bool:
    return has_role(user, *STAFF) or client.user_id == user.id


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


@router.post("/new", status_code=201, summary="Создать карточку клиента")
def create_client(
    data: ClientCreate,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    """UC-07: регистрация карточки клиента (ИНН, КПП, название, адрес)."""
    try:
        inn = validate_inn(data.inn)
        kpp = validate_kpp(data.kpp)
        credit_limit = validate_credit_limit(data.credit_limit)
    except ValueError as exc:
        raise _bad_request(exc)

    if session.exec(select(Client).where(Client.inn == inn)).first():
        raise HTTPException(409, "DuplicateInn: клиент с таким ИНН уже существует")

    if has_role(user, "manager"):
        user_id = data.user_id
        manager_id = data.manager_id if data.manager_id is not None else (
            user.id if "manager" in user.roles else None
        )
    else:
        # Клиент заводит карточку только для себя; лимит и менеджера назначает сотрудник.
        user_id = user.id
        manager_id = None
        credit_limit = 0.0

    if user_id is not None and session.exec(select(Client).where(Client.user_id == user_id)).first():
        raise HTTPException(409, "У этого пользователя уже есть карточка клиента")

    client = Client(
        user_id=user_id,
        inn=inn,
        kpp=kpp,
        name=data.name.strip(),
        legal_address=data.legal_address.strip(),
        manager_id=manager_id,
        credit_limit=credit_limit,
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


@router.get("/", summary="Список клиентов")
def list_clients(
    status: Optional[str] = None,
    manager_id: Optional[int] = None,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    query = select(Client)
    if not has_role(user, *STAFF):
        query = query.where(Client.user_id == user.id)
    if status:
        query = query.where(Client.status == status)
    if manager_id is not None:
        query = query.where(Client.manager_id == manager_id)
    return session.exec(query.order_by(Client.id)).all()


@router.get("/me", summary="Моя карточка клиента")
def my_card(user: CurrentUser = Depends(get_current_user), session: Session = Depends(get_session)):
    client = session.exec(select(Client).where(Client.user_id == user.id)).first()
    if client is None:
        raise HTTPException(404, "Карточка клиента не найдена")
    return client


@router.get("/by-user/{user_id}", summary="Карточка клиента по ID пользователя")
def card_by_user(
    user_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Используется сервисами orders и finance: реквизиты, статус и кредитный лимит."""
    if user_id != user.id and not has_role(user, *STAFF):
        raise HTTPException(403, "Недостаточно прав")
    client = session.exec(select(Client).where(Client.user_id == user_id)).first()
    if client is None:
        raise HTTPException(404, "Карточка клиента не найдена")
    return client


@router.get("/{client_id}", summary="Карточка клиента по ID")
def get_client(
    client_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    client = _get_client(session, client_id)
    if not _can_access(user, client):
        raise HTTPException(403, "Недостаточно прав")
    return client


@router.put("/edit/{client_id}", summary="Изменить данные клиента")
def edit_client(
    client_id: int,
    data: ClientEdit,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    client = _get_client(session, client_id)
    changes = data.model_dump(exclude_unset=True)
    is_staff = has_role(user, "manager")
    if not is_staff:
        if client.user_id != user.id:
            raise HTTPException(403, "Недостаточно прав")
        if set(changes) - {"legal_address"}:
            raise HTTPException(403, "Клиент может изменить только юридический адрес")
    try:
        if "kpp" in changes:
            changes["kpp"] = validate_kpp(changes["kpp"])
        if "credit_limit" in changes:
            if changes["credit_limit"] is None:
                raise ValueError("Кредитный лимит не может быть пустым")
            changes["credit_limit"] = validate_credit_limit(changes["credit_limit"])
    except ValueError as exc:
        raise _bad_request(exc)
    for key, value in changes.items():
        setattr(client, key, value)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


@router.put("/status/{client_id}", summary="Изменить статус клиента")
def change_status(
    client_id: int,
    data: StatusChange,
    user: CurrentUser = Depends(require_roles("manager")),
    session: Session = Depends(get_session),
):
    if data.status not in CLIENT_STATUSES:
        raise HTTPException(422, f"Допустимые статусы: {', '.join(CLIENT_STATUSES)}")
    client = _get_client(session, client_id)
    client.status = data.status
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


@router.delete("/delete/{client_id}", status_code=204, summary="Удалить карточку клиента")
def delete_client(
    client_id: int,
    user: CurrentUser = Depends(require_roles()),
    session: Session = Depends(get_session),
):
    client = _get_client(session, client_id)
    for contact in session.exec(select(Contact).where(Contact.client_id == client_id)).all():
        session.delete(contact)
    session.delete(client)
    session.commit()


@router.post("/{client_id}/contacts", status_code=201, summary="Добавить контакт")
def add_contact(
    client_id: int,
    data: ContactCreate,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    client = _get_client(session, client_id)
    if not has_role(user, "manager") and client.user_id != user.id:
        raise HTTPException(403, "Недостаточно прав")
    contact = Contact(client_id=client_id, **data.model_dump())
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


@router.get("/{client_id}/contacts", summary="Список контактов клиента")
def list_contacts(
    client_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    client = _get_client(session, client_id)
    if not _can_access(user, client):
        raise HTTPException(403, "Недостаточно прав")
    return session.exec(select(Contact).where(Contact.client_id == client_id)).all()


@router.delete("/contact/{contact_id}", status_code=204, summary="Удалить контакт")
def delete_contact(
    contact_id: int,
    user: CurrentUser = Depends(require_roles("client", "manager")),
    session: Session = Depends(get_session),
):
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Контакт не найден")
    client = _get_client(session, contact.client_id)
    if not has_role(user, "manager") and client.user_id != user.id:
        raise HTTPException(403, "Недостаточно прав")
    session.delete(contact)
    session.commit()
