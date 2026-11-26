"""Authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, client_ip, own_intern_profile
from app.core.crypto import blind_index
from app.core.security import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.core.timeutil import utc_now
from app.models.identity import User
from app.schemas.auth import (
    ChangePasswordRequest, CurrentUserOut, LoginRequest, TokenResponse,
)
from app.schemas.common import Message
from app.services import audit_service

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


def _user_payload(db, user: User) -> CurrentUserOut:
    intern = own_intern_profile(db, user)
    return CurrentUserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        position_title=user.position_title,
        must_change_password=user.must_change_password,
        roles=[{"name": r.name, "display_name": r.display_name} for r in user.roles],
        permissions=sorted(user.permission_codes),
        intern_id=intern.id if intern else None,
    )


def _authenticate(db, request: Request, username: str, password: str) -> User:
    from datetime import timedelta

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # Also allow signing in with the email address, matched via blind index.
        idx = blind_index(username)
        if idx:
            user = db.scalar(select(User).where(User.email_index == idx))

    # Uniform failure message: never reveal whether the username exists.
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if user is None or user.is_deleted:
        raise invalid

    now = utc_now()
    if user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60))
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account is locked for another {mins} minute(s) after repeated "
                   f"failed sign-in attempts.",
        )

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        # Every invalid attempt is recorded, not just the one that trips the
        # lockout. A single failure is noise, but a run of them across several
        # accounts from one address is the signal worth seeing, and it cannot
        # be reconstructed later from a lockout entry alone. PCI-DSS 10.2.4
        # requires invalid access attempts to be logged.
        audit_service.record(
            db, action="LOGIN_FAILED", entity_type="user", entity_id=user.id,
            actor=user,
            summary=(
                f"Failed sign-in attempt "
                f"({user.failed_login_count} of {MAX_FAILED_LOGINS})"
            ),
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
            audit_service.record(
                db, action="LOGIN_LOCKED", entity_type="user", entity_id=user.id,
                actor=user, summary="Account locked after repeated failed sign-ins",
                ip_address=client_ip(request),
            )
        db.commit()
        raise invalid

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is deactivated. Contact the System Administrator.",
        )

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit_service.record(
        db, action="LOGIN", entity_type="user", entity_id=user.id, actor=user,
        summary="Signed in", ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return user


@router.post("/login", response_model=TokenResponse, summary="Sign in (JSON)")
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = _authenticate(db, request, payload.username, payload.password)
    token = create_access_token(
        user.username, user_id=user.id, roles=sorted(user.role_names)
    )
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=_user_payload(db, user),
    )


@router.post("/token", response_model=TokenResponse, summary="Sign in (OAuth2 form)")
def login_form(
    request: Request,
    db: DbSession,
    form: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    """Form-encoded variant so the interactive API docs can authenticate."""
    user = _authenticate(db, request, form.username, form.password)
    token = create_access_token(
        user.username, user_id=user.id, roles=sorted(user.role_names)
    )
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=_user_payload(db, user),
    )


@router.get("/me", response_model=CurrentUserOut, summary="Current user")
def me(user: CurrentUser, db: DbSession) -> CurrentUserOut:
    return _user_payload(db, user)


@router.post("/change-password", response_model=Message)
def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> Message:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new password must differ from the current one.",
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    audit_service.record(
        db, action="PASSWORD_CHANGED", entity_type="user", entity_id=user.id,
        actor=user, summary="Password changed", ip_address=client_ip(request),
    )
    db.commit()
    return Message(detail="Password updated.")
