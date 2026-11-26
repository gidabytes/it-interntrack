"""FastAPI dependencies: current user, permission guards, scope checks."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.enums import RoleName
from app.core.permissions import APPROVER_ROLES
from app.core.security import AuthError, decode_access_token
from app.models.identity import User
from app.models.intern import Intern
from app.models.org import Mentor, Supervisor
from app.services.settings_service import RuleSet, load_rules

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("uid")
    user = db.get(User, user_id) if user_id else None
    if user is None or user.is_deleted or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or no longer exists.",
        )
    request.state.current_user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permissions(*codes: str, any_of: bool = True) -> Callable:
    """Guard an endpoint on permission codes.

    ``any_of`` matches the common case where a role has either the broad
    permission (``attendance.read``) or the narrow one
    (``attendance.read.assigned``).
    """

    def dependency(user: CurrentUser) -> User:
        held = user.permission_codes
        ok = (
            any(c in held for c in codes) if any_of
            else all(c in held for c in codes)
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your role does not grant permission for this action "
                    f"(requires: {', '.join(codes)})."
                ),
            )
        return user

    return dependency


def require_roles(*names: str) -> Callable:
    def dependency(user: CurrentUser) -> User:
        if not user.has_role(*names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(names)}.",
            )
        return user

    return dependency


def get_rules(db: DbSession) -> RuleSet:
    return load_rules(db)


Rules = Annotated[RuleSet, Depends(get_rules)]


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

def own_intern_profile(db: Session, user: User) -> Intern | None:
    return db.scalar(
        select(Intern).where(Intern.user_id == user.id, Intern.is_deleted.is_(False))
    )


def supervised_intern_ids(db: Session, user: User) -> set[int]:
    """Intern ids this user supervises or mentors."""
    ids: set[int] = set()
    sup = db.scalar(select(Supervisor).where(Supervisor.user_id == user.id))
    if sup:
        ids.update(i.id for i in sup.interns if not i.is_deleted)
    men = db.scalar(select(Mentor).where(Mentor.user_id == user.id))
    if men:
        ids.update(i.id for i in men.interns if not i.is_deleted)
    return ids


def assert_can_view_intern(db: Session, user: User, intern: Intern) -> None:
    """Raise unless the user may see this intern's data.

    Admin and manager see everyone. Supervisors and mentors see their own
    assignments. An intern sees only themselves.
    """
    if user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        return
    if intern.user_id == user.id:
        return
    if intern.id in supervised_intern_ids(db, user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this intern's records.",
    )


def assert_not_self_approval(db: Session, user: User, intern: Intern) -> None:
    """An intern may never approve their own attendance or hour adjustment.

    Enforced here as well as in the services, because this is the control that
    keeps the hour ledger trustworthy.
    """
    if intern.user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "An intern cannot approve their own attendance record, "
                "correction or OJT hour adjustment."
            ),
        )
    if not user.has_role(*APPROVER_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role is not permitted to approve this item.",
        )


def get_intern_or_404(db: Session, intern_id: int) -> Intern:
    intern = db.get(Intern, intern_id)
    if intern is None or intern.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intern not found."
        )
    return intern


def client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring one reverse-proxy hop (nginx)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
