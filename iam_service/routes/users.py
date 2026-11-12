from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.iam import User, UserOut, RoleUpdate, StatusUpdate
from security import ROLES, get_current_user, require_roles

users_router = APIRouter(tags=["Users"],
                         dependencies=[Depends(require_roles("admin"))])

USER_STATUSES = {"active", "blocked"}


@users_router.get("/", response_model=List[UserOut])
async def retrieve_users(session: Session = Depends(get_session)):
    return session.exec(select(User)).all()


@users_router.get("/{id}", response_model=UserOut)
async def retrieve_user(id: int, session: Session = Depends(get_session)):
    user = session.get(User, id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="User with supplied ID does not exist")
    return user


@users_router.put("/role/{id}", response_model=UserOut)
async def change_role(id: int, data: RoleUpdate,
                      admin: User = Depends(get_current_user),
                      session: Session = Depends(get_session)):
    user = session.get(User, id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="User with supplied ID does not exist")
    if data.role not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Role must be one of {sorted(ROLES)}")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot change own role")
    user.role = data.role
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@users_router.put("/status/{id}", response_model=UserOut)
async def change_status(id: int, data: StatusUpdate,
                        admin: User = Depends(get_current_user),
                        session: Session = Depends(get_session)):
    user = session.get(User, id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="User with supplied ID does not exist")
    if data.status not in USER_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Status must be one of {sorted(USER_STATUSES)}")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot change own status")
    user.status = data.status
    session.add(user)
    session.commit()
    session.refresh(user)
    return user