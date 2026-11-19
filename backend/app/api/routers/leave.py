"""Leave and absence requests."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, assert_not_self_approval,
    client_ip, get_intern_or_404, own_intern_profile, require_permissions,
    supervised_intern_ids,
)
from app.core.enums import ApprovalStatus, LeaveType, RoleName
from app.core.timeutil import utc_now
from app.models.leave import LeaveRequest
from app.schemas.common import ORMModel
from app.services import attendance_service, audit_service, ojt_engine
from app.services.schedule_resolver import resolve_range

router = APIRouter(prefix="/api/leave", tags=["Leave & Absence"])


class LeaveCreate(BaseModel):
    intern_id: int | None = None  # omitted means "me"
    leave_type: str = LeaveType.PERSONAL
    date_from: date
    date_to: date
    is_half_day: bool = False
    reason: str = Field(min_length=5, max_length=1000)
    is_official_business: bool = False


class LeaveDecision(BaseModel):
    remarks: str | None = Field(default=None, max_length=1000)
    # Approved leave does NOT credit hours unless explicitly set here or
    # enabled globally in system settings.
    is_creditable: bool = False
    credited_minutes_per_day: int | None = Field(default=None, ge=0, le=1440)


class LeaveOut(ORMModel):
    id: int
    intern_id: int
    leave_type: str
    date_from: date
    date_to: date
    is_half_day: bool
    reason: str
    approval_status: str
    approved_by_id: int | None
    decision_remarks: str | None
    is_creditable: bool
    credited_minutes_per_day: int | None
    is_official_business: bool


@router.post("", response_model=LeaveOut, status_code=status.HTTP_201_CREATED)
def create_leave(
    payload: LeaveCreate,
    db: DbSession,
    request: Request,
    user: Annotated[
        object, Depends(require_permissions("leave.request", "leave.approve"))
    ],
) -> LeaveOut:
    if payload.intern_id is None:
        intern = own_intern_profile(db, user)
        if intern is None:
            raise HTTPException(
                status_code=400,
                detail="Specify intern_id; this account has no intern profile.",
            )
    else:
        intern = get_intern_or_404(db, payload.intern_id)
        if user.has_role(RoleName.INTERN) and intern.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only file leave for yourself.",
            )
        assert_can_view_intern(db, user, intern)

    if payload.date_to < payload.date_from:
        raise HTTPException(
            status_code=400, detail="date_to cannot be before date_from."
        )

    overlap = db.scalar(
        select(LeaveRequest).where(
            LeaveRequest.intern_id == intern.id,
            LeaveRequest.is_deleted.is_(False),
            LeaveRequest.approval_status.in_(
                [ApprovalStatus.PENDING, ApprovalStatus.APPROVED]
            ),
            LeaveRequest.date_from <= payload.date_to,
            LeaveRequest.date_to >= payload.date_from,
        )
    )
    if overlap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An existing {overlap.approval_status.lower()} leave request already "
                f"covers {overlap.date_from} to {overlap.date_to}."
            ),
        )

    row = LeaveRequest(
        intern_id=intern.id,
        leave_type=payload.leave_type,
        date_from=payload.date_from,
        date_to=payload.date_to,
        is_half_day=payload.is_half_day,
        reason=payload.reason,
        is_official_business=payload.is_official_business,
        requested_by_id=user.id,
        approval_status=ApprovalStatus.PENDING,
    )
    db.add(row)
    db.flush()
    audit_service.record(
        db, action="LEAVE_REQUESTED", entity_type="leave_request", entity_id=row.id,
        actor=user,
        summary=(
            f"{intern.intern_code} requested {payload.leave_type} leave "
            f"{payload.date_from} to {payload.date_to}"
        ),
        after=row, ip_address=client_ip(request),
    )
    db.commit()
    return LeaveOut.model_validate(row)


@router.get("", response_model=list[LeaveOut])
def list_leave(
    db: DbSession,
    user: CurrentUser,
    intern_id: int | None = None,
    status_filter: str | None = None,
) -> list[LeaveOut]:
    conds = [LeaveRequest.is_deleted.is_(False)]
    if user.has_role(RoleName.INTERN):
        own = own_intern_profile(db, user)
        conds.append(LeaveRequest.intern_id == (own.id if own else -1))
    elif not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        conds.append(
            LeaveRequest.intern_id.in_(supervised_intern_ids(db, user) or {-1})
        )
    if intern_id:
        conds.append(LeaveRequest.intern_id == intern_id)
    if status_filter:
        conds.append(LeaveRequest.approval_status == status_filter)

    rows = db.scalars(
        select(LeaveRequest).where(*conds).order_by(LeaveRequest.date_from.desc())
    ).all()
    return [LeaveOut.model_validate(r) for r in rows]


@router.post("/{leave_id}/approve", response_model=LeaveOut)
def approve_leave(
    leave_id: int,
    payload: LeaveDecision,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("leave.approve"))],
) -> LeaveOut:
    """Approve leave and reflect it on the attendance calendar.

    Approved leave does not credit OJT hours unless ``is_creditable`` is set
    here, or the global policy setting is enabled. That is the spec's default
    and it is deliberately opt-in.
    """
    row = db.get(LeaveRequest, leave_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Leave request not found.")
    if row.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This request is already {row.approval_status}.",
        )

    intern = get_intern_or_404(db, row.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)
    if row.requested_by_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot approve a leave request that you submitted.",
        )

    row.approval_status = ApprovalStatus.APPROVED
    row.approved_by_id = user.id
    row.approved_at = utc_now()
    row.decision_remarks = payload.remarks
    row.is_creditable = payload.is_creditable
    row.credited_minutes_per_day = payload.credited_minutes_per_day
    db.flush()

    # Reflect the leave on each covered business date, and credit only where
    # policy allows.
    schedule = resolve_range(db, intern, row.date_from, row.date_to)
    credited_days = 0
    for d, resolved in schedule.items():
        rec = attendance_service.ensure_day_records(db, intern, d, rules=rules)
        if rec is not None:
            attendance_service.recompute_record(db, rec, rules=rules)
            ojt_engine.post_attendance(db, rec, actor_user_id=user.id, rules=rules)

        allow = payload.is_creditable or rules.bool_("approved_leave_counts_as_hours")
        if allow and resolved.has_shift and not resolved.is_rest_day:
            minutes = (
                payload.credited_minutes_per_day
                if payload.credited_minutes_per_day is not None
                else resolved.scheduled_net_minutes
            )
            if row.is_half_day:
                minutes = minutes // 2
            if minutes > 0:
                ojt_engine.post_leave_credit(
                    db, intern.id, d, minutes,
                    leave_request_id=row.id, actor_user_id=user.id,
                    description=f"Creditable {row.leave_type} leave ({d})",
                )
                credited_days += 1

    audit_service.record(
        db, action="LEAVE_APPROVED", entity_type="leave_request", entity_id=row.id,
        actor=user,
        summary=(
            f"Approved {row.leave_type} leave for {intern.intern_code} "
            f"{row.date_from} to {row.date_to}; "
            + (f"credited {credited_days} day(s)" if credited_days
               else "no OJT hours credited")
        ),
        after=row, ip_address=client_ip(request),
    )
    db.commit()
    return LeaveOut.model_validate(row)


@router.post("/{leave_id}/reject", response_model=LeaveOut)
def reject_leave(
    leave_id: int,
    payload: LeaveDecision,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("leave.approve"))],
) -> LeaveOut:
    row = db.get(LeaveRequest, leave_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Leave request not found.")
    intern = get_intern_or_404(db, row.intern_id)
    assert_can_view_intern(db, user, intern)
    assert_not_self_approval(db, user, intern)

    row.approval_status = ApprovalStatus.REJECTED
    row.approved_by_id = user.id
    row.approved_at = utc_now()
    row.decision_remarks = payload.remarks or "Rejected"
    audit_service.record(
        db, action="LEAVE_REJECTED", entity_type="leave_request", entity_id=row.id,
        actor=user, summary=f"Rejected leave: {row.decision_remarks[:150]}",
        ip_address=client_ip(request),
    )
    db.commit()
    return LeaveOut.model_validate(row)
