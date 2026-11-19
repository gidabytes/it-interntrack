"""Shift templates and the visual shift selector."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permissions
from app.models.schedule import ScheduleAssignment, ShiftTemplate
from app.schemas.common import Message
from app.schemas.schedule import (
    ShiftTemplateCreate, ShiftTemplateOut, ShiftTemplateUpdate,
)
from app.services import audit_service, shift_service

router = APIRouter(prefix="/api/shifts", tags=["Shifts & Schedules"])


@router.get("", response_model=list[ShiftTemplateOut], summary="List shift templates")
def list_shifts(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("shift.read", "schedule.read",
                                                        "schedule.read.self"))],
    include_inactive: bool = False,
) -> list[ShiftTemplateOut]:
    stmt = select(ShiftTemplate).where(ShiftTemplate.is_deleted.is_(False))
    if not include_inactive:
        stmt = stmt.where(ShiftTemplate.is_active.is_(True))
    rows = db.scalars(stmt.order_by(ShiftTemplate.start_time, ShiftTemplate.code)).all()
    return [ShiftTemplateOut.model_validate(r) for r in rows]


@router.get("/selector", summary="Visual shift selector options")
def selector(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("shift.read", "schedule.assign"))],
    include_inactive: bool = False,
) -> dict:
    """Everything the shift picker needs: code, times, overnight flag, break
    configuration and net working hours, for all 17 standard shifts plus any
    custom ones."""
    options = shift_service.shift_selector_options(db, include_inactive=include_inactive)
    return {
        "count": len(options),
        "day_shifts": [o for o in options if not o["is_overnight"]],
        "overnight_shifts": [o for o in options if o["is_overnight"]],
        "options": options,
    }


@router.post("/seed-standard", response_model=Message,
             summary="Create the 17 standard hourly templates")
def seed_standard(
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("shift.manage"))],
) -> Message:
    created = shift_service.ensure_standard_shifts(db, actor_user_id=user.id)
    audit_service.record(
        db, action="SEED", entity_type="shift_template", actor=user,
        summary=f"Seeded {created} standard shift template(s)",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(
        detail=f"{created} standard shift template(s) created; existing ones untouched."
    )


@router.post("", response_model=ShiftTemplateOut, status_code=status.HTTP_201_CREATED)
def create_shift(
    payload: ShiftTemplateCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("shift.manage"))],
) -> ShiftTemplateOut:
    if db.scalar(select(ShiftTemplate).where(ShiftTemplate.code == payload.code)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Shift code {payload.code} already exists.",
        )
    tpl = ShiftTemplate(**payload.model_dump(), created_by_id=user.id)
    if tpl.unpaid_break_minutes >= tpl.elapsed_minutes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The unpaid break ({tpl.unpaid_break_minutes} min) is not shorter "
                f"than the shift ({tpl.elapsed_minutes} min); net working time would "
                f"be zero or negative."
            ),
        )
    db.add(tpl)
    db.flush()
    audit_service.record(
        db, action="CREATE", entity_type="shift_template", entity_id=tpl.id, actor=user,
        summary=f"Created shift {tpl.code} {tpl.start_time}-{tpl.end_time}",
        after=tpl, ip_address=client_ip(request),
    )
    db.commit()
    return ShiftTemplateOut.model_validate(tpl)


@router.patch("/{shift_id}", response_model=ShiftTemplateOut)
def update_shift(
    shift_id: int,
    payload: ShiftTemplateUpdate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("shift.manage"))],
) -> ShiftTemplateOut:
    """Edit a template.

    Historical attendance is NOT recomputed: every attendance record holds its
    own snapshot of the configuration that applied when it was created, so past
    figures stay as they were. The change affects future sessions only.
    """
    tpl = db.get(ShiftTemplate, shift_id)
    if tpl is None or tpl.is_deleted:
        raise HTTPException(status_code=404, detail="Shift template not found.")

    before = audit_service._snapshot(tpl)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(tpl, k, v)
    tpl.updated_by_id = user.id

    if tpl.unpaid_break_minutes >= tpl.elapsed_minutes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The unpaid break would leave zero or negative net working time.",
        )

    in_use = db.scalar(
        select(ScheduleAssignment).where(
            ScheduleAssignment.shift_template_id == tpl.id,
            ScheduleAssignment.is_deleted.is_(False),
        )
    )
    entry = audit_service.record(
        db, action="UPDATE", entity_type="shift_template", entity_id=tpl.id, actor=user,
        summary=(
            f"Updated {', '.join(sorted(data.keys()))}"
            + (" (template is in active use; historical records keep their snapshot)"
               if in_use else "")
        ),
        after=tpl, ip_address=client_ip(request),
    )
    entry.before_state = before
    db.commit()
    return ShiftTemplateOut.model_validate(tpl)


@router.delete("/{shift_id}", response_model=Message,
               summary="Deactivate a shift template")
def deactivate_shift(
    shift_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("shift.manage"))],
) -> Message:
    """Deactivate rather than delete, so historical records keep a valid
    reference."""
    tpl = db.get(ShiftTemplate, shift_id)
    if tpl is None or tpl.is_deleted:
        raise HTTPException(status_code=404, detail="Shift template not found.")
    tpl.is_active = False
    tpl.updated_by_id = user.id
    audit_service.record(
        db, action="DEACTIVATE", entity_type="shift_template", entity_id=tpl.id,
        actor=user, summary=f"Deactivated shift {tpl.code}",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(detail=f"Shift {tpl.code} deactivated and can no longer be assigned.")
