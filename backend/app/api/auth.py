from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.security import (
    clear_session_cookie,
    client_ip,
    create_token,
    get_current_user,
    hash_password,
    rate_limiter,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    email: str


def _throttle(request: Request, email: str) -> None:
    s = get_settings()
    rate_limiter.check(f"ip:{client_ip(request)}", s.login_max_attempts * 4, s.login_window_seconds)
    rate_limiter.check(f"mail:{email}", s.login_max_attempts, s.login_window_seconds)


@router.get("/status")
def registration_status(db: Session = Depends(get_db)) -> dict:
    first = db.scalar(select(func.count(User.id))) == 0
    return {"registration_open": first or get_settings().allow_registration}


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: Credentials, request: Request, response: Response,
             db: Session = Depends(get_db)) -> User:
    email = body.email.lower()
    _throttle(request, email)
    first = db.scalar(select(func.count(User.id))) == 0
    if not (first or get_settings().allow_registration):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Les inscriptions sont fermées")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Inscription impossible avec cet e-mail")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    set_session_cookie(response, create_token(user))
    return user


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> User:
    email = body.email.lower()
    _throttle(request, email)
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(body.password, user.password_hash if user else None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    set_session_cookie(response, create_token(user))
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)) -> None:
    user.token_version += 1  # invalide tous les jetons déjà émis
    db.merge(user)
    db.commit()
    clear_session_cookie(response)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, request: Request, response: Response,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    _throttle(request, user.email)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe actuel incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le nouveau mot de passe doit être différent")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # déconnecte toutes les autres sessions
    db.commit()
    set_session_cookie(response, create_token(user))  # et conserve celle-ci


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
