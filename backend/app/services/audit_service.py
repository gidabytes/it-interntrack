"""Immutable audit trail.

Insert-only. Rows are never updated or deleted, which is what makes the trail
usable as evidence of who changed an approved attendance record or an OJT hour
adjustment.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.models.identity import User
from app.models.system import AuditLog

# Fields never written into the audit trail, even if present on the model.
REDACTED_FIELDS = {"password_hash", "signature_hash"}


def _snapshot(obj: Any, fields: list[str] | None = None) -> dict | None:
    """Serialisable snapshot of a model instance.

    Encrypted attributes are read through the ORM, so they would land in the
    audit log as plaintext. They are redacted by name instead: the trail
    records THAT a contact number changed, not what it changed to.
    """
    if obj is None:
        return None
    from sqlalchemy import inspect as sa_inspect

    try:
        mapper = sa_inspect(obj).mapper
    except Exception:  # pragma: no cover - not a mapped object
        return {"value": str(obj)}

    out: dict[str, Any] = {}
    names = fields or [c.key for c in mapper.column_attrs]
    for name in names:
        if name in REDACTED_FIELDS:
            out[name] = "[REDACTED]"
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        col = mapper.columns.get(name)
        if col is not None and type(col.type).__name__ in (
            "EncryptedString", "EncryptedText"
        ):
            out[name] = "[ENCRYPTED]" if value else None
            continue
        if hasattr(value, "isoformat"):
            out[name] = value.isoformat()
        elif isinstance(value, (str, int, float, bool, type(None))):
            out[name] = value
        elif isinstance(value, (dict, list)):
            out[name] = value
        else:
            out[name] = str(value)
    return out


def snapshot(obj: Any, fields: list[str] | None = None) -> dict | None:
    """Capture an object's state before it is mutated.

    A caller that edits a row in place has already lost the old values by the
    time it calls record(), so it takes this first and passes the result as
    `before`.
    """
    return _snapshot(obj, fields)


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    actor: User | None = None,
    summary: str = "",
    before: Any = None,
    after: Any = None,
    fields: list[str] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        created_at=utc_now(),
        actor_user_id=actor.id if actor else None,
        actor_username=actor.username if actor else None,
        actor_roles=",".join(sorted(actor.role_names)) if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:500],
        before_state=_snapshot(before, fields),
        after_state=_snapshot(after, fields),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:300] or None,
        request_id=request_id,
    )
    db.add(entry)
    db.flush()
    return entry


def trail_for(
    db: Session, entity_type: str, entity_id: int, *, limit: int = 100
) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        ).all()
    )
