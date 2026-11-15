"""Authentification : Argon2id, JWT en cookie httpOnly, anti-CSRF, limitation de débit."""

import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

COOKIE_NAME = "pea_session"
CSRF_HEADER = "x-requested-with"
_ALGO = "HS256"
_hasher = PasswordHasher()  # Argon2id, paramètres OWASP par défaut
# Hash factice : égalise le temps de réponse quand l'e-mail est inconnu.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except VerificationError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.signing_key(), algorithm=_ALGO)


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=s.access_token_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/api",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/api")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized
    try:
        payload = jwt.decode(
            token,
            get_settings().signing_key(),
            algorithms=[_ALGO],
            options={"require": ["exp", "sub", "ver"]},
        )
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None
    if user is None or user.token_version != payload["ver"]:
        raise unauthorized
    return user


def require_csrf_header(request: Request) -> None:
    """Défense CSRF : un en-tête personnalisé ne peut pas être émis par un formulaire
    inter-origines, et le CORS n'autorise que les origines configurées."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and CSRF_HEADER not in request.headers:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "En-tête X-Requested-With manquant")


class RateLimiter:
    """Fenêtre glissante en mémoire (suffisant pour une instance unique)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Trop de tentatives, réessayez plus tard",
                    headers={"Retry-After": str(int(window - (now - q[0])) + 1)},
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
