"""OJT progress, hour ledger and completion forecast."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, assert_not_self_approval,
    client_ip, get_intern_or_404, own_intern_profile, require_permissions,
)
from app.core.enums import RoleName
from app.core.timeutil import minutes_to_hours
from app.models.ojt import OJTHourLedger
from app.schemas.common import Message
from app.services import audit_service, forecast_service, ojt_engine

router = APIRouter(prefix="/api/ojt", tags=["OJT Hours & Progress"])


def _progress_payload(db, intern, rules) -> dict:
    progress = ojt_engine.get_progress(db, intern)
    forecast = forecast_service.forecast_for_intern(
        db, intern, rules=rules, progress=progress
    )

    # Attendance-derived counters for the progress page.
    from sqlalchemy import func
    from app.core.enums import AttendanceStatus
    from app.models.attendance import AttendanceRecord

    rows = db.execute(
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .where(
            AttendanceRecord.intern_id == intern.id,
            AttendanceRecord.is_deleted.is_(False),
        )
        .group_by(AttendanceRecord.status)
    ).all()
    counts = {s: c for s, c in rows}
    present = sum(
        counts.get(s, 0) for s in (
            AttendanceStatus.PRESENT, AttendanceStatus.LATE,
            AttendanceStatus.UNDERTIME, AttendanceStatus.HALF_DAY,
        )
    )
    absences = counts.get(AttendanceStatus.ABSENT, 0)
    expected = present + absences + counts.get(
        AttendanceStatus.INCOMPLETE_TIME_RECORD, 0
    )
    overtime = db.scalar(
        select(func.coalesce(func.sum(AttendanceRecord.approved_overtime_minutes), 0))
        .where(
            AttendanceRecord.intern_id == intern.id,
            AttendanceRecord.is_deleted.is_(False),
        )
    ) or 0

    return {
        "intern": {
            "id": intern.id,
            "intern_code": intern.intern_code,
            "name": intern.full_name,
            "status": intern.status,
            "team": intern.team.name if intern.team else None,
            "supervisor": (
                intern.supervisor.user.full_name
                if intern.supervisor and intern.supervisor.user else None
            ),
            "mentor": (
                intern.mentor.user.full_name
                if intern.mentor and intern.mentor.user else None
            ),
            "ojt_start_date": intern.ojt_start_date.isoformat(),
            "school_submission_deadline": (
                intern.school_submission_deadline.isoformat()
                if intern.school_submission_deadline else None
            ),
        },
        "progress": progress.as_dict(),
        "attendance": {
            "days_present": present,
            "absences": absences,
            "late_arrivals": counts.get(AttendanceStatus.LATE, 0),
            "undertime_instances": counts.get(AttendanceStatus.UNDERTIME, 0),
            "half_days": counts.get(AttendanceStatus.HALF_DAY, 0),
            "incomplete_records": counts.get(
                AttendanceStatus.INCOMPLETE_TIME_RECORD, 0
            ),
            "total_overtime_hours": minutes_to_hours(int(overtime)),
            "attendance_rate": (
                round(present / expected * 100, 2) if expected else 0.0
            ),
        },
        "forecast": forecast.as_dict(),
        "milestones": [
            {
                "threshold": t, "label": label,
                "reached": progress.completion_percentage >= t,
            }
            for t, label in ojt_engine.MILESTONES
        ],
    }


@router.get("/progress/me", summary="Own OJT progress")
def my_progress(db: DbSession, rules: Rules, user: CurrentUser) -> dict:
    intern = own_intern_profile(db, user)
    if intern is None:
        raise HTTPException(
            status_code=404, detail="This account is not linked to an intern profile."
        )
    return _progress_payload(db, intern, rules)


@router.get("/progress/{intern_id}", summary="OJT progress for an intern")
def intern_progress(
    intern_id: int, db: DbSession, rules: Rules, user: CurrentUser
) -> dict:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    return _progress_payload(db, intern, rules)


@router.get("/ledger/{intern_id}", summary="OJT hour ledger")
def ledger(
    intern_id: int,
    db: DbSession,
    user: CurrentUser,
    include_reversed: bool = True,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """The full audit trail behind an intern's hours.

    Reversed rows and reversal rows are included by default: the point of the
    ledger is that you can see what a figure used to be and why it changed.
    """
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)

    conds = [OJTHourLedger.intern_id == intern_id]
    if not include_reversed:
        conds += [
            OJTHourLedger.is_reversed.is_(False),
            OJTHourLedger.reversal_of_id.is_(None),
        ]
    if date_from:
        conds.append(OJTHourLedger.business_date >= date_from)
    if date_to:
        conds.append(OJTHourLedger.business_date <= date_to)

    rows = db.scalars(
        select(OJTHourLedger).where(*conds).order_by(
            OJTHourLedger.business_date, OJTHourLedger.id
        )
    ).all()
    progress = ojt_engine.get_progress(db, intern)

    return {
        "intern_id": intern.id,
        "intern_code": intern.intern_code,
        "progress": progress.as_dict(),
        "entry_count": len(rows),
        "entries": [
            {
                "id": r.id,
                "business_date": r.business_date.isoformat(),
                "source_type": r.source_type,
                "source_id": r.source_id,
                "minutes_delta": r.minutes_delta,
                "hours_delta": r.hours_delta,
                "actual_minutes": r.actual_minutes,
                "is_approved": r.is_approved,
                "is_reversed": r.is_reversed,
                "reversal_of_id": r.reversal_of_id,
                "counts_toward_total": r.is_approved and not r.is_reversed
                and r.reversal_of_id is None,
                "description": r.description,
                "computation_note": r.computation_note,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/adjustments", summary="Post a manual OJT hour adjustment")
def post_adjustment(
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("ojt.adjust"))],
    intern_id: int = Query(...),
    business_date: date = Query(...),
    minutes_delta: int = Query(..., description="Signed; negative deducts hours"),
    description: str = Query(..., min_length=5),
) -> dict:
    """Post an adjustment directly to the ledger.

    Never edits an existing entry: the adjustment is its own ledger row, so the
    original attendance posting and the adjustment are both visible.
    """
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)

    entry = ojt_engine.post_adjustment(
        db, intern.id, business_date, minutes_delta,
        actor_user_id=user.id, description=description,
    )
    audit_service.record(
        db, action="OJT_ADJUSTMENT", entity_type="ojt_hour_ledger",
        entity_id=entry.id, actor=user,
        summary=(
            f"{minutes_delta:+d}m adjustment for {intern.intern_code} "
            f"on {business_date}: {description[:150]}"
        ),
        after=entry, ip_address=client_ip(request),
    )
    db.commit()
    progress = ojt_engine.get_progress(db, intern)
    return {
        "ledger_entry_id": entry.id,
        "minutes_delta": entry.minutes_delta,
        "progress": progress.as_dict(),
    }


@router.post("/ledger/{entry_id}/reverse", summary="Reverse a ledger entry")
def reverse(
    entry_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("ojt.adjust"))],
    reason: str = Query(..., min_length=5),
) -> dict:
    entry = db.get(OJTHourLedger, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found.")
    intern = get_intern_or_404(db, entry.intern_id)
    assert_not_self_approval(db, user, intern)
    if entry.is_reversed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ledger entry is already reversed.",
        )
    counter = ojt_engine.reverse_entry(
        db, entry, actor_user_id=user.id, reason=reason
    )
    audit_service.record(
        db, action="OJT_REVERSAL", entity_type="ojt_hour_ledger", entity_id=entry.id,
        actor=user, summary=f"Reversed {entry.minutes_delta:+d}m: {reason[:150]}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "reversed_entry_id": entry.id,
        "counter_entry_id": counter.id,
        "progress": ojt_engine.get_progress(db, intern).as_dict(),
    }


@router.get("/forecast", summary="Completion forecast for all interns")
def forecast_list(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("ojt.read"))],
    team_id: int | None = None,
    supervisor_id: int | None = None,
    risk: str | None = Query(
        default=None,
        description="ON_TRACK | AT_RISK | WILL_MISS_DEADLINE | COMPLETED | NO_DATA",
    ),
    today: date | None = None,
) -> dict:
    """Who will not finish by their school submission deadline."""
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        from app.models.org import Supervisor

        own = db.scalar(select(Supervisor).where(Supervisor.user_id == user.id))
        if own and supervisor_id is None:
            supervisor_id = own.id

    rows = forecast_service.forecast_all(
        db, rules=rules, today=today, team_id=team_id,
        supervisor_id=supervisor_id,
        risk_filter=[risk] if risk else None,
    )
    return {
        "count": len(rows),
        "flagged": sum(
            1 for r in rows if r.risk in ("AT_RISK", "WILL_MISS_DEADLINE")
        ),
        "forecasts": [r.as_dict() for r in rows],
    }


@router.post("/rebuild-ledger/{intern_id}", response_model=Message,
             summary="Repair: re-sync the ledger from attendance")
def rebuild(
    intern_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("ojt.adjust"))],
) -> Message:
    """Re-post every attendance record to the ledger.

    A repair tool. Running it on a healthy ledger changes nothing, because
    posting is idempotent.
    """
    intern = get_intern_or_404(db, intern_id)
    stats = ojt_engine.rebuild_ledger_for_intern(db, intern, actor_user_id=user.id)
    audit_service.record(
        db, action="LEDGER_REBUILD", entity_type="intern", entity_id=intern.id,
        actor=user,
        summary=(
            f"Rebuilt ledger: {stats['records']} records, "
            f"{stats['posted']} re-posted, {stats['unchanged']} unchanged"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(
        detail=(
            f"Processed {stats['records']} attendance record(s): "
            f"{stats['posted']} re-posted, {stats['unchanged']} already correct."
        )
    )
