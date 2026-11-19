"""Password hashing and JWT issuance.

bcrypt is used directly rather than through passlib: passlib 1.7.x reads
``bcrypt.__about__.__version__``, which bcrypt 5.x removed, and its bcrypt
backend then mishandles the 72-byte limit and raises on perfectly valid
passwords.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.timeutil import utc_now

# bcrypt truncates at 72 bytes. Rather than silently ignoring the tail of a
# long passphrase, reject it so the user knows their full password is not in use.
BCRYPT_MAX_BYTES = 72


class AuthError(Exception):
    def __init__(self, message: str, code: str = "AUTH_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise AuthError(
            f"Password must be at most {BCRYPT_MAX_BYTES} bytes "
            f"({len(raw)} given). bcrypt silently truncates beyond this.",
            code="PASSWORD_TOO_LONG",
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        raw = password.encode("utf-8")
        if len(raw) > BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed or legacy hash: treat as a failed attempt, never a pass.
        return False


def create_access_token(
    subject: str,
    *,
    user_id: int,
    roles: list[str],
    expires_minutes: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    now = utc_now()
    expire = now + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "uid": user_id,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.APP_NAME,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise AuthError("Invalid or expired token.", code="INVALID_TOKEN") from exc
