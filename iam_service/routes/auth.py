import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from database.connection import get_session
from models.iam import User, RegisterIn, LoginIn, TokenOut, UserOut
from security import (TOKEN_TTL_MINUTES, create_access_token, get_current_user,
                      hash_password, verify_password)

auth_router = APIRouter(tags=["Auth"])

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@auth_router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, session: Session = Depends(get_session)) -> dict:
    email = data.email.strip().lower()
    errors = []
    if not EMAIL_PATTERN.match(email):
        errors.append("email has invalid format")
    if len(data.password) < 8:
        errors.append("password must be at least 8 characters")
    if not (data.inn.isdigit() and len(data.inn) in (10, 12)):
        errors.append("inn must contain 10 or 12 digits")
    if not data.company_name.strip():
        errors.append("company_name must not be empty")
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="; ".join(errors))
    if session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="User with this email already exists")
    user = User(email=email,
                password_hash=hash_password(data.password),
                full_name=data.full_name,
                company_name=data.company_name.strip(),
                inn=data.inn,
                role="client")
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"message": "User registered successfully", "id": user.id}


@auth_router.post("/login", response_model=TokenOut)
async def login(data: LoginIn, session: Session = Depends(get_session)):
    email = data.email.strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Wrong email or password")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="User is blocked")
    return TokenOut(access_token=create_access_token(user),
                    expires_in=TOKEN_TTL_MINUTES * 60,
                    role=user.role)


@auth_router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user