"""Digital timesheets: generate, submit, sign, verify."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, client_ip,
    get_intern_or_404, own_intern_profile, require_permissions, supervised_intern_ids,
)
from app.core.enums import RoleName, TimesheetPeriod, TimesheetStatus
from app.models.timesheet import Timesheet
from app.schemas.common import ORMModel
from app.services import audit_service, timesheet_service
from app.services.timesheet_service import TimesheetError

router = APIRouter(prefix="/api/timesheets", tags=["Timesheets"])


class GenerateRequest(BaseModel):
    intern_id: int
    period_type: str = TimesheetPeriod.MONTHLY
    anchor_date: date
    force_new_revision: bool = False


class SignRequest(BaseModel):
    remarks: str | None = Field(default=None, max_length=1000)
    # Signing a period with unapproved records is refused by default.
    require_all_approved: bool = True


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class TimesheetOut(ORMModel):
    id: int
    intern_id: int
    reference_no: str
    period_type: str
    period_start: date
    period_end: date
    revision: int
    total_days_scheduled: int
    total_days_present: int
    total_days_absent: int
    total_days_late: int
    total_scheduled_minutes: int
    total_actual_minutes: int
    total_creditable_minutes: int
    total_late_minutes: int
    total_undertime_minutes: int
    total_overtime_minutes: int
    cumulative_approved_minutes: int
    required_minutes: int
    remaining_minutes: int
    status: str
    signed_by_name: str | None
    signed_by_position: str | None
    signature_hash: str | None
    is_locked: bool
    superseded_by_id: int | None


def _handle(exc: TimesheetError) -> HTTPException:
    code = (
        status.HTTP_409_CONFLICT if exc.code in ("ALREADY_SIGNED", "UNAPPROVED_RECORDS")
        else status.HTTP_403_FORBIDDEN if "FORBIDDEN" in exc.code or "AUTHORISED" in exc.code
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message})


@router.post("/generate", response_model=TimesheetOut,
             summary="Generate or regenerate a timesheet")
def generate(
    payload: GenerateRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("timesheet.generate"))],
) -> TimesheetOut:
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    ts = timesheet_service.generate(
        db, intern, period_type=payload.period_type, anchor=payload.anchor_date,
        actor_user_id=user.id, rules=rules,
        force_new_revision=payload.force_new_revision,
    )
    audit_service.record(
        db, action="TIMESHEET_GENERATED", entity_type="timesheet", entity_id=ts.id,
        actor=user,
        summary=(
            f"Generated {ts.reference_no}: {ts.total_creditable_minutes / 60:.2f}h "
            f"creditable over {ts.total_days_present} day(s)"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return TimesheetOut.model_validate(ts)


@router.get("", response_model=list[TimesheetOut])
def list_timesheets(
    db: DbSession,
    user: CurrentUser,
    intern_id: int | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    include_superseded: bool = False,
) -> list[TimesheetOut]:
    conds = [Timesheet.is_deleted.is_(False)]
    if user.has_role(RoleName.INTERN):
        own = own_intern_profile(db, user)
        conds.append(Timesheet.intern_id == (own.id if own else -1))
    elif not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        conds.append(Timesheet.intern_id.in_(supervised_intern_ids(db, user) or {-1}))
    if intern_id:
        conds.append(Timesheet.intern_id == intern_id)
    if status_filter:
        conds.append(Timesheet.status == status_filter)
    if not include_superseded:
        conds.append(Timesheet.superseded_by_id.is_(None))

    rows = db.scalars(
        select(Timesheet).where(*conds).order_by(
            Timesheet.period_start.desc(), Timesheet.revision.desc()
        )
    ).all()
    return [TimesheetOut.model_validate(r) for r in rows]


@router.get("/pending-signature", response_model=list[TimesheetOut],
            summary="Timesheets awaiting signature")
def pending_signature(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("timesheet.sign"))],
) -> list[TimesheetOut]:
    conds = [
        Timesheet.is_deleted.is_(False),
        Timesheet.status == TimesheetStatus.SUBMITTED,
        Timesheet.superseded_by_id.is_(None),
    ]
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        conds.append(Timesheet.intern_id.in_(supervised_intern_ids(db, user) or {-1}))
    rows = db.scalars(
        select(Timesheet).where(*conds).order_by(Timesheet.submitted_at)
    ).all()
    return [TimesheetOut.model_validate(r) for r in rows]


@router.get("/{timesheet_id}", summary="Timesheet with line items")
def get_timesheet(
    timesheet_id: int, db: DbSession, rules: Rules, user: CurrentUser
) -> dict:
    ts = db.get(Timesheet, timesheet_id)
    if ts is None or ts.is_deleted:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    assert_can_view_intern(db, user, get_intern_or_404(db, ts.intern_id))
    return timesheet_service.to_printable(db, ts, rules)


@router.post("/{timesheet_id}/submit", response_model=TimesheetOut,
             summary="Submit own timesheet for review")
def submit(
    timesheet_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[
        object, Depends(require_permissions("timesheet.submit", "timesheet.generate"))
    ],
) -> TimesheetOut:
    ts = db.get(Timesheet, timesheet_id)
    if ts is None or ts.is_deleted:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    intern = get_intern_or_404(db, ts.intern_id)
    if user.has_role(RoleName.INTERN) and intern.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only submit your own timesheet.",
        )
    assert_can_view_intern(db, user, intern)
    try:
        timesheet_service.submit(db, ts, actor_user_id=user.id)
    except TimesheetError as exc:
        raise _handle(exc) from exc
    audit_service.record(
        db, action="TIMESHEET_SUBMITTED", entity_type="timesheet", entity_id=ts.id,
        actor=user, summary=f"Submitted {ts.reference_no} for signature",
        ip_address=client_ip(request),
    )
    db.commit()
    return TimesheetOut.model_validate(ts)


@router.post("/{timesheet_id}/sign", response_model=TimesheetOut,
             summary="Supervisor signs the timesheet")
def sign(
    timesheet_id: int,
    payload: SignRequest,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("timesheet.sign"))],
) -> TimesheetOut:
    """Sign a timesheet.

    Signing stamps the signer and a SHA-256 over the frozen content, and locks
    the underlying attendance records. Later corrections create a new revision
    rather than altering what was signed.
    """
    ts = db.get(Timesheet, timesheet_id)
    if ts is None or ts.is_deleted:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    intern = get_intern_or_404(db, ts.intern_id)
    assert_can_view_intern(db, user, intern)
    try:
        timesheet_service.sign(
            db, ts, signer=user, remarks=payload.remarks,
            require_all_approved=payload.require_all_approved,
        )
    except TimesheetError as exc:
        raise _handle(exc) from exc

    audit_service.record(
        db, action="TIMESHEET_SIGNED", entity_type="timesheet", entity_id=ts.id,
        actor=user,
        summary=(
            f"Signed {ts.reference_no} ({ts.total_creditable_minutes / 60:.2f}h); "
            f"hash {ts.signature_hash[:16]}..."
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return TimesheetOut.model_validate(ts)


@router.post("/{timesheet_id}/reject", response_model=TimesheetOut)
def reject(
    timesheet_id: int,
    payload: RejectRequest,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("timesheet.sign"))],
) -> TimesheetOut:
    ts = db.get(Timesheet, timesheet_id)
    if ts is None or ts.is_deleted:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    assert_can_view_intern(db, user, get_intern_or_404(db, ts.intern_id))
    try:
        timesheet_service.reject(db, ts, actor_user_id=user.id, reason=payload.reason)
    except TimesheetError as exc:
        raise _handle(exc) from exc
    audit_service.record(
        db, action="TIMESHEET_REJECTED", entity_type="timesheet", entity_id=ts.id,
        actor=user, summary=f"Rejected {ts.reference_no}: {payload.reason[:150]}",
        ip_address=client_ip(request),
    )
    db.commit()
    return TimesheetOut.model_validate(ts)


@router.get("/{timesheet_id}/verify", summary="Verify a signature")
def verify(timesheet_id: int, db: DbSession, user: CurrentUser) -> dict:
    """Confirm a signed timesheet has not been altered since signing."""
    ts = db.get(Timesheet, timesheet_id)
    if ts is None or ts.is_deleted:
        raise HTTPException(status_code=404, detail="Timesheet not found.")
    assert_can_view_intern(db, user, get_intern_or_404(db, ts.intern_id))
    return timesheet_service.verify_signature(ts)
