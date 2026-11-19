"""Attendance: digital timekeeping, manual entry, approvals, corrections."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, assert_not_self_approval,
    client_ip, get_intern_or_404, own_intern_profile, require_permissions,
    supervised_intern_ids,
)
from app.core.enums import ApprovalStatus, AttendanceStatus, RoleName
from app.core.timeutil import to_utc, utc_now
from app.models.attendance import AttendanceAdjustment, AttendanceRecord
from app.schemas.attendance import (
    ApprovalRequest, AttendanceOut, BreakRequest, CorrectionOut, CorrectionRequest,
    ManualAttendanceRequest, PunchRequest, RejectionRequest,
)
from app.schemas.common import Message, Page
from app.services import attendance_service, audit_service, ojt_engine
from app.services.attendance_service import AttendanceError

router = APIRouter(prefix="/api/attendance", tags=["Attendance"])

AUDIT_FIELDS = [
    "business_date", "session_sequence", "time_in", "time_out", "shift_code",
    "status", "approval_status", "actual_net_minutes", "creditable_minutes",
    "late_minutes", "undertime_minutes", "approved_overtime_minutes", "is_locked",
]


def _handle(exc: AttendanceError) -> HTTPException:
    code = (
        status.HTTP_409_CONFLICT
        if exc.code in ("DUPLICATE_TIME_IN", "OVERLAPPING_ATTENDANCE", "BREAK_ALREADY_OPEN")
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message})


def _require_own_intern(db, user) -> object:
    intern = own_intern_profile(db, user)
    if intern is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This account is not linked to an intern profile.",
        )
    return intern


# ---------------------------------------------------------------------------
# Digital timekeeping (intern self-service)
# ---------------------------------------------------------------------------

@router.post("/time-in", response_model=AttendanceOut, summary="Record own time in")
def time_in(
    payload: PunchRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.punch"))],
) -> AttendanceOut:
    intern = _require_own_intern(db, user)
    # An intern may not back-date or future-date their own punch.
    if payload.timestamp is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You cannot set the timestamp on your own punch. Ask your "
                "supervisor to record or correct the entry."
            ),
        )
    try:
        rec = attendance_service.time_in(
            db, intern, actor_user_id=user.id, ip_address=client_ip(request),
            rules=rules,
        )
    except AttendanceError as exc:
        raise _handle(exc) from exc

    audit_service.record(
        db, action="TIME_IN", entity_type="attendance_record", entity_id=rec.id,
        actor=user,
        summary=(
            f"{intern.intern_code} timed in for {rec.business_date} "
            f"({rec.shift_code or 'no schedule'})"
        ),
        after=rec, fields=AUDIT_FIELDS, ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return AttendanceOut.model_validate(rec)


@router.post("/time-out", response_model=AttendanceOut, summary="Record own time out")
def time_out(
    payload: PunchRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.punch"))],
) -> AttendanceOut:
    intern = _require_own_intern(db, user)
    if payload.timestamp is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot set the timestamp on your own punch.",
        )
    try:
        rec = attendance_service.time_out(
            db, intern, actor_user_id=user.id, ip_address=client_ip(request),
            rules=rules,
        )
    except AttendanceError as exc:
        raise _handle(exc) from exc

    # Keep the ledger in step immediately; it posts as PENDING until approved.
    ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
    audit_service.record(
        db, action="TIME_OUT", entity_type="attendance_record", entity_id=rec.id,
        actor=user,
        summary=(
            f"{intern.intern_code} timed out: {rec.actual_net_minutes}m rendered, "
            f"{rec.creditable_minutes}m creditable ({rec.status})"
        ),
        after=rec, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    db.commit()
    return AttendanceOut.model_validate(rec)


@router.post("/break/start", summary="Start a break")
def break_start(
    payload: BreakRequest,
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("attendance.punch"))],
) -> dict:
    intern = _require_own_intern(db, user)
    try:
        brk = attendance_service.break_start(
            db, intern, break_type=payload.break_type, is_paid=payload.is_paid,
            rules=rules,
        )
    except AttendanceError as exc:
        raise _handle(exc) from exc
    db.commit()
    return {"id": brk.id, "break_start": brk.break_start.isoformat(),
            "break_type": brk.break_type, "is_paid": brk.is_paid}


@router.post("/break/end", summary="End a break")
def break_end(
    payload: BreakRequest,
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("attendance.punch"))],
) -> dict:
    intern = _require_own_intern(db, user)
    try:
        brk = attendance_service.break_end(db, intern, rules=rules)
    except AttendanceError as exc:
        raise _handle(exc) from exc
    db.commit()
    return {"id": brk.id, "break_start": brk.break_start.isoformat(),
            "break_end": brk.break_end.isoformat(),
            "duration_minutes": brk.duration_minutes}


@router.get("/my-status", summary="Own current timekeeping status")
def my_status(db: DbSession, rules: Rules, user: CurrentUser) -> dict:
    """What the intern's own time-clock widget shows."""
    from app.services.schedule_resolver import resolve_for_date
    from app.core.timeutil import business_date_of

    intern = _require_own_intern(db, user)
    open_session = attendance_service.get_open_session(db, intern.id)
    today = business_date_of(utc_now())
    resolved = resolve_for_date(db, intern, today)

    open_break = None
    if open_session:
        ob = next((b for b in open_session.breaks if b.break_end is None), None)
        open_break = ob.break_start.isoformat() if ob else None

    return {
        "intern_code": intern.intern_code,
        "server_time": utc_now().isoformat(),
        "is_timed_in": open_session is not None,
        "on_break_since": open_break,
        "open_session": (
            AttendanceOut.model_validate(open_session).model_dump(mode="json")
            if open_session else None
        ),
        "today_schedule": resolved.as_dict(),
        "can_time_in": open_session is None,
        "can_time_out": open_session is not None,
    }


# ---------------------------------------------------------------------------
# Reading records
# ---------------------------------------------------------------------------

@router.get("", response_model=Page[AttendanceOut], summary="List attendance records")
def list_records(
    db: DbSession,
    user: CurrentUser,
    intern_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    approval_status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> Page[AttendanceOut]:
    from sqlalchemy import func

    conds = [AttendanceRecord.is_deleted.is_(False)]

    # Scope by role.
    if user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        if intern_id:
            conds.append(AttendanceRecord.intern_id == intern_id)
    elif user.has_role(RoleName.INTERN):
        own = _require_own_intern(db, user)
        conds.append(AttendanceRecord.intern_id == own.id)
    else:
        allowed = supervised_intern_ids(db, user)
        if intern_id:
            if intern_id not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not supervise this intern.",
                )
            conds.append(AttendanceRecord.intern_id == intern_id)
        else:
            conds.append(AttendanceRecord.intern_id.in_(allowed or {-1}))

    if date_from:
        conds.append(AttendanceRecord.business_date >= date_from)
    if date_to:
        conds.append(AttendanceRecord.business_date <= date_to)
    if status_filter:
        conds.append(AttendanceRecord.status == status_filter)
    if approval_status:
        conds.append(AttendanceRecord.approval_status == approval_status)

    total = db.scalar(select(func.count(AttendanceRecord.id)).where(*conds)) or 0
    rows = db.scalars(
        select(AttendanceRecord)
        .where(*conds)
        .order_by(
            AttendanceRecord.business_date.desc(), AttendanceRecord.session_sequence
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[AttendanceOut](
        items=[AttendanceOut.model_validate(r) for r in rows],
        total=int(total), page=page, page_size=page_size,
    )


@router.get("/{record_id}", response_model=AttendanceOut)
def get_record(record_id: int, db: DbSession, user: CurrentUser) -> AttendanceOut:
    rec = db.get(AttendanceRecord, record_id)
    if rec is None or rec.is_deleted:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
    assert_can_view_intern(db, user, get_intern_or_404(db, rec.intern_id))
    return AttendanceOut.model_validate(rec)


# ---------------------------------------------------------------------------
# Manual entry and approval (supervisor)
# ---------------------------------------------------------------------------

@router.post("/manual", response_model=AttendanceOut,
             summary="Record or correct attendance for an intern")
def manual_entry(
    payload: ManualAttendanceRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.manage"))],
) -> AttendanceOut:
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    # A supervisor who is also an intern cannot hand-enter their own attendance.
    if intern.user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot manually record your own attendance.",
        )

    existing = attendance_service.get_records_for_date(
        db, intern.id, payload.business_date
    )
    before = (
        audit_service._snapshot(existing[0], AUDIT_FIELDS) if existing else None
    )

    try:
        rec = attendance_service.manual_entry(
            db, intern, payload.business_date,
            time_in_at=payload.time_in, time_out_at=payload.time_out,
            actor_user_id=user.id, status_override=payload.status_override,
            remarks=payload.remarks, overtime_approved=payload.overtime_approved,
            rules=rules, session_sequence=payload.session_sequence,
        )
    except AttendanceError as exc:
        raise _handle(exc) from exc

    ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
    entry = audit_service.record(
        db, action="MANUAL_ENTRY", entity_type="attendance_record", entity_id=rec.id,
        actor=user,
        summary=(
            f"Manually recorded {intern.intern_code} {rec.business_date}: "
            f"{rec.status}, {rec.creditable_minutes}m creditable"
            + (f" - {payload.remarks}" if payload.remarks else "")
        ),
        after=rec, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    entry.before_state = before
    db.commit()
    return AttendanceOut.model_validate(rec)


@router.post("/{record_id}/approve", response_model=AttendanceOut)
def approve_record(
    record_id: int,
    payload: ApprovalRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
) -> AttendanceOut:
    """Approve attendance, making its hours count toward OJT.

    An intern can never approve their own record: that guard is what keeps the
    hour ledger trustworthy, so it is enforced here and in the service layer.
    """
    rec = db.get(AttendanceRecord, record_id)
    if rec is None or rec.is_deleted:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
    intern = get_intern_or_404(db, rec.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)

    if rec.time_in is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There is no time-in on this record to approve.",
        )
    if rec.time_out is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This session has no time out. Record the missing time out first, "
                "then approve."
            ),
        )

    before = audit_service._snapshot(rec, AUDIT_FIELDS)

    if payload.approve_overtime and rec.overtime_minutes > 0:
        rec.overtime_approved = True
        rec.overtime_approved_by_id = user.id
        attendance_service.recompute_record(db, rec, rules=rules)

    rec.approval_status = ApprovalStatus.APPROVED
    rec.approved_by_id = user.id
    rec.approved_at = utc_now()
    if payload.remarks:
        rec.supervisor_remarks = payload.remarks

    ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
    ojt_engine.approve_ledger_for_record(db, rec, actor_user_id=user.id)

    entry = audit_service.record(
        db, action="APPROVE", entity_type="attendance_record", entity_id=rec.id,
        actor=user,
        summary=(
            f"Approved {intern.intern_code} {rec.business_date}: "
            f"{rec.creditable_minutes}m credited"
            + (f", overtime {rec.approved_overtime_minutes}m approved"
               if rec.approved_overtime_minutes else "")
        ),
        after=rec, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    entry.before_state = before
    db.commit()
    return AttendanceOut.model_validate(rec)


@router.post("/{record_id}/reject", response_model=AttendanceOut)
def reject_record(
    record_id: int,
    payload: RejectionRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
) -> AttendanceOut:
    rec = db.get(AttendanceRecord, record_id)
    if rec is None or rec.is_deleted:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
    intern = get_intern_or_404(db, rec.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)

    before = audit_service._snapshot(rec, AUDIT_FIELDS)
    rec.approval_status = ApprovalStatus.REJECTED
    rec.approved_by_id = user.id
    rec.approved_at = utc_now()
    rec.supervisor_remarks = payload.reason

    # Rejected hours must stop counting: re-post so the ledger entry is
    # reversed rather than silently left approved.
    ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
    entry = audit_service.record(
        db, action="REJECT", entity_type="attendance_record", entity_id=rec.id,
        actor=user, summary=f"Rejected: {payload.reason}",
        after=rec, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    entry.before_state = before
    db.commit()
    return AttendanceOut.model_validate(rec)


@router.post("/bulk-approve", summary="Approve many records at once")
def bulk_approve(
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
    record_ids: list[int] = Query(default=[]),
    intern_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Approve by explicit ids, or by intern and date range.

    Each record is still checked individually, and anything that cannot be
    approved is reported rather than skipped silently.
    """
    conds = [
        AttendanceRecord.is_deleted.is_(False),
        AttendanceRecord.approval_status == ApprovalStatus.PENDING,
        AttendanceRecord.time_in.is_not(None),
        AttendanceRecord.time_out.is_not(None),
    ]
    if record_ids:
        conds.append(AttendanceRecord.id.in_(record_ids))
    else:
        if not intern_id:
            raise HTTPException(
                status_code=400,
                detail="Provide record_ids, or intern_id with a date range.",
            )
        conds.append(AttendanceRecord.intern_id == intern_id)
        if date_from:
            conds.append(AttendanceRecord.business_date >= date_from)
        if date_to:
            conds.append(AttendanceRecord.business_date <= date_to)

    approved, failed = [], []
    for rec in db.scalars(select(AttendanceRecord).where(*conds)).all():
        intern = db.get(AttendanceRecord, rec.id).intern
        try:
            assert_can_view_intern(db, user, intern)
            assert_not_self_approval(db, user, intern)
        except HTTPException as exc:
            failed.append({"record_id": rec.id, "reason": str(exc.detail)})
            continue
        rec.approval_status = ApprovalStatus.APPROVED
        rec.approved_by_id = user.id
        rec.approved_at = utc_now()
        ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
        ojt_engine.approve_ledger_for_record(db, rec, actor_user_id=user.id)
        approved.append(rec.id)

    audit_service.record(
        db, action="BULK_APPROVE", entity_type="attendance_record", actor=user,
        summary=f"Bulk approved {len(approved)} record(s), {len(failed)} failed",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"approved_count": len(approved), "approved_ids": approved,
            "failed_count": len(failed), "failed": failed}


@router.post("/finalize-day", summary="Close out a business date")
def finalize_day(
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.manage"))],
    business_date: date = Query(...),
) -> dict:
    """Mark absences and flag incomplete records for a business date.

    Only finalises where the shift has ended and the absence grace period has
    elapsed, so running it early never marks a working intern absent.
    """
    counts = attendance_service.finalize_absences(db, business_date, rules=rules)
    audit_service.record(
        db, action="FINALIZE_DAY", entity_type="attendance_record", actor=user,
        summary=(
            f"Finalised {business_date}: {counts['absent']} absent, "
            f"{counts['incomplete']} incomplete"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return {"business_date": business_date.isoformat(), **counts}


# ---------------------------------------------------------------------------
# Correction requests
# ---------------------------------------------------------------------------

@router.post("/corrections", response_model=CorrectionOut,
             status_code=status.HTTP_201_CREATED)
def request_correction(
    payload: CorrectionRequest,
    db: DbSession,
    request: Request,
    user: Annotated[
        object, Depends(require_permissions("attendance.request_correction",
                                            "attendance.manage"))
    ],
) -> CorrectionOut:
    intern = get_intern_or_404(db, payload.intern_id)
    # An intern may only file corrections against their own records.
    if user.has_role(RoleName.INTERN) and intern.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only request corrections for your own attendance.",
        )
    assert_can_view_intern(db, user, intern)

    rec = None
    if payload.attendance_record_id:
        rec = db.get(AttendanceRecord, payload.attendance_record_id)
        if rec is None or rec.intern_id != intern.id:
            raise HTTPException(
                status_code=404, detail="Attendance record not found for this intern."
            )

    row = AttendanceAdjustment(
        attendance_record_id=payload.attendance_record_id,
        intern_id=intern.id,
        business_date=payload.business_date,
        adjustment_type=payload.adjustment_type,
        reason=payload.reason,
        requested_time_in=to_utc(payload.requested_time_in) if payload.requested_time_in else None,
        requested_time_out=to_utc(payload.requested_time_out) if payload.requested_time_out else None,
        requested_status=payload.requested_status,
        requested_minutes_delta=payload.requested_minutes_delta,
        previous_time_in=rec.time_in if rec else None,
        previous_time_out=rec.time_out if rec else None,
        previous_status=rec.status if rec else None,
        previous_creditable_minutes=rec.creditable_minutes if rec else None,
        requested_by_id=user.id,
        approval_status=ApprovalStatus.PENDING,
    )
    db.add(row)
    db.flush()
    audit_service.record(
        db, action="CORRECTION_REQUESTED", entity_type="attendance_adjustment",
        entity_id=row.id, actor=user,
        summary=(
            f"{intern.intern_code} {payload.business_date} "
            f"{payload.adjustment_type}: {payload.reason[:120]}"
        ),
        after=row, ip_address=client_ip(request),
    )
    db.commit()
    return CorrectionOut.model_validate(row)


@router.get("/corrections/pending", response_model=list[CorrectionOut])
def pending_corrections(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
) -> list[CorrectionOut]:
    conds = [
        AttendanceAdjustment.is_deleted.is_(False),
        AttendanceAdjustment.approval_status == ApprovalStatus.PENDING,
    ]
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        conds.append(
            AttendanceAdjustment.intern_id.in_(supervised_intern_ids(db, user) or {-1})
        )
    rows = db.scalars(
        select(AttendanceAdjustment)
        .where(*conds)
        .order_by(AttendanceAdjustment.created_at)
    ).all()
    return [CorrectionOut.model_validate(r) for r in rows]


@router.post("/corrections/{correction_id}/approve", response_model=CorrectionOut)
def approve_correction(
    correction_id: int,
    payload: ApprovalRequest,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
) -> CorrectionOut:
    """Approve a correction and apply it.

    The adjustment row is the permanent record of why the figures changed; the
    attendance record is recomputed and the ledger reversed-and-reposted so no
    hours are double counted.
    """
    row = db.get(AttendanceAdjustment, correction_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Correction request not found.")
    if row.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This request is already {row.approval_status}.",
        )

    intern = get_intern_or_404(db, row.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)
    # The requester may not approve their own request, whatever their role.
    if row.requested_by_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot approve a correction request that you submitted.",
        )

    rec = (
        db.get(AttendanceRecord, row.attendance_record_id)
        if row.attendance_record_id else None
    )

    if row.adjustment_type == "HOURS_CREDIT":
        if row.requested_minutes_delta:
            ojt_engine.post_adjustment(
                db, intern.id, row.business_date, row.requested_minutes_delta,
                actor_user_id=user.id,
                description=f"Approved correction #{row.id}: {row.reason[:160]}",
                source_id=row.id,
            )
    else:
        if rec is None:
            # Creating the record the correction refers to.
            rec = attendance_service.manual_entry(
                db, intern, row.business_date,
                time_in_at=row.requested_time_in,
                time_out_at=row.requested_time_out,
                actor_user_id=user.id, rules=rules,
                remarks=f"Created by approved correction #{row.id}",
            )
        else:
            # Locked by a signed timesheet? The lock must be lifted deliberately.
            was_locked = rec.is_locked
            rec.is_locked = False
            if row.requested_time_in is not None:
                rec.time_in = row.requested_time_in
            if row.requested_time_out is not None:
                rec.time_out = row.requested_time_out
            if row.adjustment_type == "OVERTIME":
                rec.overtime_approved = True
                rec.overtime_approved_by_id = user.id
            attendance_service.recompute_record(db, rec, rules=rules)
            if row.requested_status:
                rec.status = row.requested_status
            rec.supervisor_remarks = (
                f"Corrected via request #{row.id}: {row.reason[:200]}"
            )
            rec.approval_status = ApprovalStatus.APPROVED
            rec.approved_by_id = user.id
            rec.approved_at = utc_now()
            rec.is_locked = was_locked
        ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)
        ojt_engine.approve_ledger_for_record(db, rec, actor_user_id=user.id)

    row.approval_status = ApprovalStatus.APPROVED
    row.approved_by_id = user.id
    row.approved_at = utc_now()
    row.decision_remarks = payload.remarks

    audit_service.record(
        db, action="CORRECTION_APPROVED", entity_type="attendance_adjustment",
        entity_id=row.id, actor=user,
        summary=(
            f"Approved {row.adjustment_type} correction for {intern.intern_code} "
            f"{row.business_date}"
            + (f"; record now {rec.creditable_minutes}m creditable" if rec else "")
        ),
        after=row, ip_address=client_ip(request),
    )
    db.commit()
    return CorrectionOut.model_validate(row)


@router.post("/corrections/{correction_id}/reject", response_model=CorrectionOut)
def reject_correction(
    correction_id: int,
    payload: RejectionRequest,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("attendance.approve"))],
) -> CorrectionOut:
    row = db.get(AttendanceAdjustment, correction_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Correction request not found.")
    intern = get_intern_or_404(db, row.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)

    row.approval_status = ApprovalStatus.REJECTED
    row.approved_by_id = user.id
    row.approved_at = utc_now()
    row.decision_remarks = payload.reason
    audit_service.record(
        db, action="CORRECTION_REJECTED", entity_type="attendance_adjustment",
        entity_id=row.id, actor=user, summary=f"Rejected: {payload.reason}",
        ip_address=client_ip(request),
    )
    db.commit()
    return CorrectionOut.model_validate(row)
