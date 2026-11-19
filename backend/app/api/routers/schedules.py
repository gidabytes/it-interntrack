"""Schedule assignment, conflict validation and the calendar view."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, client_ip,
    get_intern_or_404, own_intern_profile, require_permissions,
)
from app.core.enums import ApprovalStatus, AssignmentType, RoleName
from app.core.timeutil import utc_now
from app.models.schedule import Holiday, RestDay, ScheduleAssignment, ShiftTemplate
from app.schemas.common import Message
from app.schemas.schedule import (
    AssignmentCreate, AssignmentOut, BulkAssignmentCreate, HolidayCreate, RestDayCreate,
)
from app.services import audit_service, shift_service
from app.services.schedule_resolver import resolve_range

router = APIRouter(prefix="/api/schedules", tags=["Shifts & Schedules"])


def _template_or_404(db, template_id: int | None) -> ShiftTemplate | None:
    if template_id is None:
        return None
    tpl = db.get(ShiftTemplate, template_id)
    if tpl is None or tpl.is_deleted:
        raise HTTPException(status_code=404, detail="Shift template not found.")
    return tpl


@router.post("/validate", summary="Dry-run conflict validation")
def validate(
    payload: AssignmentCreate,
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("schedule.assign"))],
) -> dict:
    """Check an assignment without saving it.

    The UI calls this as the supervisor fills the form, so conflicts surface
    before anything is written.
    """
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    tpl = _template_or_404(db, payload.shift_template_id)
    result = shift_service.validate_assignment(
        db, intern, tpl,
        assignment_type=payload.assignment_type,
        day_of_week=payload.day_of_week,
        specific_date=payload.specific_date,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        is_rest_day=payload.is_rest_day,
        min_rest_hours=rules.int_("min_rest_hours_between_shifts"),
    )
    return result.as_dict()


@router.post("/assignments", response_model=AssignmentOut,
             status_code=status.HTTP_201_CREATED, summary="Assign a schedule")
def create_assignment(
    payload: AssignmentCreate,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("schedule.assign"))],
) -> AssignmentOut:
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    tpl = _template_or_404(db, payload.shift_template_id)

    result = shift_service.validate_assignment(
        db, intern, tpl,
        assignment_type=payload.assignment_type,
        day_of_week=payload.day_of_week,
        specific_date=payload.specific_date,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        is_rest_day=payload.is_rest_day,
        min_rest_hours=rules.int_("min_rest_hours_between_shifts"),
    )

    # Errors are always blocking. Warnings (insufficient rest) may be overridden
    # by a supervisor who states a reason - that is the authorised exception
    # the spec calls for.
    if not result.is_valid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "This assignment conflicts with the existing schedule.",
                **result.as_dict(),
            },
        )
    if result.warnings and not payload.conflict_override_reason:
        if rules.bool_("block_on_schedule_conflict"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "This assignment raises warnings. Supply "
                        "conflict_override_reason to save it as an authorised "
                        "exception."
                    ),
                    **result.as_dict(),
                },
            )

    # Schedule changes may require approval before taking effect.
    needs_approval = (
        rules.bool_("require_approval_for_schedule_change")
        and not user.has_role(
            RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER, RoleName.IT_SUPERVISOR
        )
    )

    row = ScheduleAssignment(
        **payload.model_dump(exclude={"conflict_override_reason"}),
        conflict_override_reason=payload.conflict_override_reason,
        approval_status=(
            ApprovalStatus.PENDING if needs_approval else ApprovalStatus.APPROVED
        ),
        approved_by_id=None if needs_approval else user.id,
        approved_at=None if needs_approval else date.today(),
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit_service.record(
        db, action="CREATE", entity_type="schedule_assignment", entity_id=row.id,
        actor=user,
        summary=(
            f"Assigned {tpl.code if tpl else 'REST DAY'} to {intern.intern_code} "
            f"({row.assignment_type}, dow={row.day_of_week}, from {row.effective_from})"
            + (f" OVERRIDE: {payload.conflict_override_reason}"
               if payload.conflict_override_reason else "")
        ),
        after=row, ip_address=client_ip(request),
    )
    db.commit()
    return AssignmentOut.model_validate(row)


@router.post("/assignments/bulk", summary="Bulk assign to many interns")
def bulk_assign(
    payload: BulkAssignmentCreate,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("schedule.assign"))],
) -> dict:
    """Assign one pattern to many interns.

    Each intern is validated independently and reported individually, so one
    conflicting intern does not silently drop the rest of the batch.
    """
    tpl = _template_or_404(db, payload.shift_template_id)
    created, skipped = [], []

    for intern_id in payload.intern_ids:
        intern = get_intern_or_404(db, intern_id)
        try:
            assert_can_view_intern(db, user, intern)
        except HTTPException:
            skipped.append({"intern_id": intern_id, "reason": "No access"})
            continue

        for dow in payload.days_of_week:
            result = shift_service.validate_assignment(
                db, intern, tpl,
                assignment_type=payload.assignment_type,
                day_of_week=dow,
                specific_date=None,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                is_rest_day=False,
                min_rest_hours=rules.int_("min_rest_hours_between_shifts"),
            )
            blocking = (not result.is_valid) or (
                result.warnings and not payload.allow_conflicts
            )
            if blocking:
                skipped.append({
                    "intern_id": intern_id,
                    "intern_code": intern.intern_code,
                    "day_of_week": dow,
                    "reason": [f.message for f in (result.errors or result.warnings)],
                })
                continue
            row = ScheduleAssignment(
                intern_id=intern.id,
                shift_template_id=tpl.id if tpl else None,
                assignment_type=payload.assignment_type,
                day_of_week=dow,
                rotation_cycle_weeks=payload.rotation_cycle_weeks,
                rotation_week_index=payload.rotation_week_index,
                rotation_anchor_date=payload.rotation_anchor_date,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                approval_status=ApprovalStatus.APPROVED,
                approved_by_id=user.id,
                conflict_override_reason=payload.conflict_override_reason,
                created_by_id=user.id,
            )
            db.add(row)
            created.append({"intern_code": intern.intern_code, "day_of_week": dow})

        for dow in payload.rest_days_of_week:
            db.add(RestDay(
                intern_id=intern.id, day_of_week=dow,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
            ))

    db.flush()
    audit_service.record(
        db, action="BULK_ASSIGN", entity_type="schedule_assignment", actor=user,
        summary=(
            f"Bulk assigned {tpl.code if tpl else 'rest days'} to "
            f"{len(payload.intern_ids)} intern(s): {len(created)} created, "
            f"{len(skipped)} skipped"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped": skipped,
    }


@router.get("/assignments/{intern_id}", response_model=list[AssignmentOut])
def list_assignments(
    intern_id: int, db: DbSession, user: CurrentUser
) -> list[AssignmentOut]:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    rows = db.scalars(
        select(ScheduleAssignment)
        .where(
            ScheduleAssignment.intern_id == intern_id,
            ScheduleAssignment.is_deleted.is_(False),
        )
        .order_by(ScheduleAssignment.effective_from.desc(), ScheduleAssignment.id)
    ).all()
    return [AssignmentOut.model_validate(r) for r in rows]


@router.post("/assignments/{assignment_id}/approve", response_model=AssignmentOut)
def approve_assignment(
    assignment_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("schedule.approve"))],
) -> AssignmentOut:
    row = db.get(ScheduleAssignment, assignment_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    row.approval_status = ApprovalStatus.APPROVED
    row.approved_by_id = user.id
    row.approved_at = date.today()
    audit_service.record(
        db, action="APPROVE", entity_type="schedule_assignment", entity_id=row.id,
        actor=user, summary="Approved schedule modification",
        ip_address=client_ip(request),
    )
    db.commit()
    return AssignmentOut.model_validate(row)


@router.delete("/assignments/{assignment_id}", response_model=Message)
def delete_assignment(
    assignment_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("schedule.assign"))],
) -> Message:
    row = db.get(ScheduleAssignment, assignment_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    row.is_deleted = True
    row.deleted_at = utc_now()
    row.deleted_by_id = user.id
    audit_service.record(
        db, action="DELETE", entity_type="schedule_assignment", entity_id=row.id,
        actor=user, summary="Removed schedule assignment",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(detail="Assignment removed. Past attendance is unaffected.")


@router.get("/calendar/{intern_id}", summary="Resolved schedule calendar")
def calendar(
    intern_id: int,
    db: DbSession,
    user: CurrentUser,
    date_from: date = Query(...),
    date_to: date = Query(...),
) -> dict:
    """The schedule actually in force for each date, after precedence.

    This is what the calendar view renders, and what the attendance engine
    binds punches against.
    """
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    if (date_to - date_from).days > 366:
        raise HTTPException(
            status_code=400, detail="Range cannot exceed 366 days."
        )
    resolved = resolve_range(db, intern, date_from, date_to)
    days = [resolved[d].as_dict() for d in sorted(resolved.keys())]
    return {
        "intern_id": intern.id,
        "intern_code": intern.intern_code,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "working_days": sum(1 for d in days if d["is_working_day"]),
        "rest_days": sum(1 for d in days if d["is_rest_day"]),
        "holidays": sum(1 for d in days if d["is_holiday"]),
        "leave_days": sum(1 for d in days if d["is_on_leave"]),
        "scheduled_hours": round(
            sum(d["scheduled_net_minutes"] for d in days) / 60, 2
        ),
        "days": days,
    }


@router.get("/my-calendar", summary="Own schedule calendar")
def my_calendar(
    db: DbSession,
    user: CurrentUser,
    date_from: date = Query(...),
    date_to: date = Query(...),
) -> dict:
    intern = own_intern_profile(db, user)
    if intern is None:
        raise HTTPException(
            status_code=404, detail="This account is not linked to an intern profile."
        )
    return calendar(intern.id, db, user, date_from, date_to)


# --- Holidays and rest days ------------------------------------------------

@router.post("/holidays", status_code=status.HTTP_201_CREATED)
def create_holiday(
    payload: HolidayCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("holiday.manage"))],
) -> dict:
    row = Holiday(**payload.model_dump())
    db.add(row)
    db.flush()
    audit_service.record(
        db, action="CREATE", entity_type="holiday", entity_id=row.id, actor=user,
        summary=f"Added holiday {row.holiday_date} {row.name}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": row.id, "holiday_date": str(row.holiday_date), "name": row.name}


@router.get("/holidays")
def list_holidays(
    db: DbSession, user: CurrentUser,
    year: int | None = None, department_id: int | None = None,
) -> list[dict]:
    stmt = select(Holiday).where(Holiday.is_deleted.is_(False))
    if year:
        stmt = stmt.where(
            Holiday.holiday_date >= date(year, 1, 1),
            Holiday.holiday_date <= date(year, 12, 31),
        )
    if department_id:
        stmt = stmt.where(Holiday.department_id.in_([department_id, None]))
    return [
        {
            "id": h.id, "holiday_date": str(h.holiday_date), "name": h.name,
            "holiday_type": h.holiday_type, "department_id": h.department_id,
            "is_non_working": h.is_non_working, "is_creditable": h.is_creditable,
        }
        for h in db.scalars(stmt.order_by(Holiday.holiday_date)).all()
    ]


@router.post("/rest-days", status_code=status.HTTP_201_CREATED)
def create_rest_day(
    payload: RestDayCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("schedule.assign"))],
) -> dict:
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    row = RestDay(**payload.model_dump())
    db.add(row)
    db.flush()
    audit_service.record(
        db, action="CREATE", entity_type="rest_day", entity_id=row.id, actor=user,
        summary=f"Set weekday {row.day_of_week} as rest day for {intern.intern_code}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": row.id, "intern_id": row.intern_id, "day_of_week": row.day_of_week}
