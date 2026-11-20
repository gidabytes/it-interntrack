"""Administration: users, roles, settings, audit trail, encryption keys."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permissions
from app.core.crypto import encryption_enabled
from app.core.enums import RoleName
from app.core.permissions import PERMISSIONS, ROLE_DISPLAY, ROLE_PERMISSIONS
from app.core.security import hash_password
from app.models.identity import Role, User
from app.models.org import Mentor, Supervisor
from app.models.system import AuditLog, SystemSetting
from app.schemas.common import Message
from app.services import audit_service, settings_service

router = APIRouter(prefix="/api/admin", tags=["Administration"])


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=180)
    password: str = Field(min_length=10, max_length=72)
    roles: list[str] = Field(min_length=1)
    employee_id: str | None = None
    contact_number: str | None = None
    position_title: str | None = None
    department_id: int | None = None
    must_change_password: bool = True
    # Create the matching Supervisor / Mentor record automatically.
    create_org_record: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    contact_number: str | None = None
    position_title: str | None = None
    department_id: int | None = None
    is_active: bool | None = None
    roles: list[str] | None = None


class SettingUpdate(BaseModel):
    value: Any


@router.get("/permissions", summary="Permission catalogue and role map")
def permissions(
    user: Annotated[object, Depends(require_permissions("role.manage",
                                                        "settings.manage"))],
) -> dict:
    return {
        "permissions": [
            {"code": c, "description": d} for c, d in sorted(PERMISSIONS.items())
        ],
        "roles": [
            {
                "name": r,
                "display_name": ROLE_DISPLAY.get(r, r),
                "permission_count": len(p),
                "permissions": sorted(p),
            }
            for r, p in ROLE_PERMISSIONS.items()
        ],
    }


@router.get("/users", summary="List users")
def list_users(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("user.manage"))],
    include_inactive: bool = False,
) -> list[dict]:
    stmt = select(User).where(User.is_deleted.is_(False))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    return [
        {
            "id": u.id, "username": u.username, "full_name": u.full_name,
            "email": u.email, "employee_id": u.employee_id,
            "position_title": u.position_title, "is_active": u.is_active,
            "must_change_password": u.must_change_password,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "roles": sorted(u.role_names),
        }
        for u in db.scalars(stmt.order_by(User.full_name)).all()
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Create a user")
def create_user(
    payload: UserCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("user.manage"))],
) -> dict:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' is already taken.",
        )
    from app.core.crypto import blind_index

    idx = blind_index(payload.email)
    if idx and db.scalar(select(User).where(User.email_index == idx)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered.",
        )

    roles = []
    for name in payload.roles:
        role = db.scalar(select(Role).where(Role.name == name))
        if role is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown role '{name}'. Valid roles: "
                    f"{', '.join(sorted(ROLE_PERMISSIONS))}"
                ),
            )
        roles.append(role)

    u = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        employee_id=payload.employee_id,
        contact_number=payload.contact_number,
        position_title=payload.position_title,
        department_id=payload.department_id,
        must_change_password=payload.must_change_password,
        created_by_id=user.id,
    )
    u.sync_lookup_indexes()
    u.roles = roles
    db.add(u)
    db.flush()

    # Keep the org-side record in step so assignment screens work immediately.
    if payload.create_org_record:
        if RoleName.IT_SUPERVISOR in payload.roles:
            db.add(Supervisor(user_id=u.id, department_id=payload.department_id))
        if RoleName.IT_MENTOR in payload.roles:
            db.add(Mentor(user_id=u.id))
        db.flush()

    audit_service.record(
        db, action="CREATE", entity_type="user", entity_id=u.id, actor=user,
        summary=f"Created user {u.username} with roles {', '.join(payload.roles)}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": u.id, "username": u.username, "roles": sorted(u.role_names)}


@router.patch("/users/{user_id}", summary="Update a user")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("user.manage"))],
) -> dict:
    target = db.get(User, user_id)
    if target is None or target.is_deleted:
        raise HTTPException(status_code=404, detail="User not found.")

    data = payload.model_dump(exclude_unset=True)
    role_names = data.pop("roles", None)

    # Guard against removing the last administrator.
    if role_names is not None or data.get("is_active") is False:
        if RoleName.SYSTEM_ADMINISTRATOR in target.role_names:
            losing_admin = (
                role_names is not None
                and RoleName.SYSTEM_ADMINISTRATOR not in role_names
            ) or data.get("is_active") is False
            if losing_admin:
                admins = db.scalars(
                    select(User).where(
                        User.is_active.is_(True), User.is_deleted.is_(False)
                    )
                ).all()
                remaining = [
                    a for a in admins
                    if RoleName.SYSTEM_ADMINISTRATOR in a.role_names
                    and a.id != target.id
                ]
                if not remaining:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "This is the only active System Administrator. "
                            "Grant the role to another account first."
                        ),
                    )

    for k, v in data.items():
        setattr(target, k, v)
    if "email" in data:
        target.sync_lookup_indexes()
    if role_names is not None:
        roles = []
        for name in role_names:
            role = db.scalar(select(Role).where(Role.name == name))
            if role is None:
                raise HTTPException(status_code=400, detail=f"Unknown role '{name}'.")
            roles.append(role)
        target.roles = roles
    target.updated_by_id = user.id

    audit_service.record(
        db, action="UPDATE", entity_type="user", entity_id=target.id, actor=user,
        summary=f"Updated {', '.join(sorted(set(data) | ({'roles'} if role_names else set())))}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": target.id, "username": target.username,
            "is_active": target.is_active, "roles": sorted(target.role_names)}


@router.post("/users/{user_id}/reset-password", response_model=Message)
def reset_password(
    user_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("user.manage"))],
    new_password: str = Query(..., min_length=10, max_length=72),
) -> Message:
    target = db.get(User, user_id)
    if target is None or target.is_deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    target.failed_login_count = 0
    target.locked_until = None
    audit_service.record(
        db, action="PASSWORD_RESET", entity_type="user", entity_id=target.id,
        actor=user, summary=f"Reset password for {target.username}",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(
        detail=f"Password reset for {target.username}; they must change it on next sign-in."
    )


@router.get("/settings", summary="System settings")
def list_settings(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("settings.manage"))],
    category: str | None = None,
) -> dict:
    settings_service.ensure_defaults(db)
    db.commit()
    stmt = select(SystemSetting)
    if category:
        stmt = stmt.where(SystemSetting.category == category)
    rows = db.scalars(stmt.order_by(SystemSetting.category, SystemSetting.key)).all()
    grouped: dict[str, list[dict]] = {}
    for s in rows:
        grouped.setdefault(s.category, []).append({
            "key": s.key, "value": s.value, "value_type": s.value_type,
            "description": s.description, "is_editable": s.is_editable,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })
    return {"categories": sorted(grouped), "settings": grouped}


@router.put("/settings/{key}", summary="Change a setting")
def update_setting(
    key: str,
    payload: SettingUpdate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("settings.manage"))],
) -> dict:
    """Change a computation rule.

    Changing a rule affects FUTURE computations and any record that is
    subsequently recomputed; it does not silently rewrite history, because each
    attendance record carries its own shift snapshot.
    """
    try:
        before = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
        old = before.value if before else None
        setting = settings_service.set_setting(
            db, key, payload.value, actor_user_id=user.id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    audit_service.record(
        db, action="SETTING_CHANGED", entity_type="system_setting",
        entity_id=setting.id, actor=user,
        summary=f"{key}: {old} -> {setting.value}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"key": setting.key, "value": setting.value,
            "value_type": setting.value_type}


@router.get("/audit", summary="Audit trail")
def audit_trail(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("audit.read"))],
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_user_id: int | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[dict]:
    conds = []
    if entity_type:
        conds.append(AuditLog.entity_type == entity_type)
    if entity_id:
        conds.append(AuditLog.entity_id == entity_id)
    if actor_user_id:
        conds.append(AuditLog.actor_user_id == actor_user_id)
    if action:
        conds.append(AuditLog.action == action)
    if date_from:
        conds.append(AuditLog.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conds.append(AuditLog.created_at <= datetime.combine(date_to, datetime.max.time()))

    rows = db.scalars(
        select(AuditLog).where(*conds).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": a.id, "created_at": a.created_at.isoformat(),
            "actor": a.actor_username, "actor_roles": a.actor_roles,
            "action": a.action, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "summary": a.summary,
            "before_state": a.before_state, "after_state": a.after_state,
            "ip_address": a.ip_address,
        }
        for a in rows
    ]


@router.get("/system-status", summary="Deployment self-check")
def system_status(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("settings.manage"))],
) -> dict:
    """Readiness checks an administrator should see before going live."""
    from sqlalchemy import func

    from app.core.config import settings
    from app.models.schedule import ShiftTemplate

    shift_count = db.scalar(
        select(func.count(ShiftTemplate.id)).where(
            ShiftTemplate.is_deleted.is_(False)
        )
    ) or 0
    admin_count = len([
        u for u in db.scalars(
            select(User).where(User.is_active.is_(True), User.is_deleted.is_(False))
        ).all()
        if RoleName.SYSTEM_ADMINISTRATOR in u.role_names
    ])
    weak_secret = settings.SECRET_KEY.startswith("CHANGE-ME")

    checks = [
        {
            "check": "Field encryption configured",
            "ok": encryption_enabled(),
            "detail": (
                "Fernet keys loaded; PII is encrypted at rest."
                if encryption_enabled()
                else "FERNET_KEYS is empty - intern PII is stored in PLAINTEXT."
            ),
        },
        {
            "check": "JWT secret changed from the default",
            "ok": not weak_secret,
            "detail": (
                "SECRET_KEY still holds the shipped placeholder. Generate one with "
                "`openssl rand -hex 32`."
                if weak_secret else "SECRET_KEY has been set."
            ),
        },
        {
            "check": "Shift templates seeded",
            "ok": shift_count >= 17,
            "detail": f"{shift_count} template(s) present (17 standard expected).",
        },
        {
            "check": "At least one active System Administrator",
            "ok": admin_count >= 1,
            "detail": f"{admin_count} active administrator account(s).",
        },
        {
            "check": "Production database",
            "ok": not settings.is_sqlite,
            "detail": (
                "SQLite is in use. Acceptable for a single instance; "
                "PostgreSQL is recommended for production."
                if settings.is_sqlite
                else f"{settings.database_backend} connection configured."
            ),
        },
    ]

    if settings.is_supabase and "supabase.co" in settings.DATABASE_URL.lower() \
            and "localhost" not in settings.DATABASE_URL.lower():
        checks.append({
            "check": "Intern personal data stays on the internal network",
            "ok": False,
            "detail": (
                "The database is hosted on Supabase Cloud, so intern names, "
                "email addresses, contact numbers and emergency contacts are "
                "stored outside your network. Field-level encryption still "
                "applies, but this is a data-residency decision - see "
                "docs/SUPABASE.md. Self-hosted Supabase or on-premises "
                "PostgreSQL keeps the data inside the network."
            ),
        })
    return {
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "business_timezone": settings.BUSINESS_TIMEZONE,
        "database": settings.database_backend,
        "debug": settings.DEBUG,
        "all_passed": all(c["ok"] for c in checks),
        "checks": checks,
    }
