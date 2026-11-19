"""Individual OJT digital timesheets, signed by the IT Supervisor.

A timesheet is the document the school actually accepts, so it has to behave
like a signed document rather than a live view:

* generating one FREEZES the day-by-day line items and totals into the row;
* signing stamps the signer, the moment, and a SHA-256 over the frozen content;
* a signed timesheet is never edited. A later attendance correction produces
  revision 2, with revision 1 kept and marked superseded.

That is what makes a signature meaningful: the hash proves the figures the
supervisor signed are the figures still on record.
"""
from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import (
    ApprovalStatus, AttendanceStatus, TimesheetPeriod, TimesheetStatus,
)
from app.core.timeutil import (
    date_range, fmt_hhmm, minutes_to_hours, to_business, utc_now,
)
from app.models.attendance import AttendanceRecord
from app.models.identity import User
from app.models.intern import Intern
from app.models.timesheet import Timesheet
from app.services.ojt_engine import get_progress
from app.services.schedule_resolver import resolve_range
from app.services.settings_service import RuleSet, load_rules

# Statuses that count as the intern having reported for duty.
PRESENT_STATUSES = {
    AttendanceStatus.PRESENT,
    AttendanceStatus.LATE,
    AttendanceStatus.UNDERTIME,
    AttendanceStatus.HALF_DAY,
}


class TimesheetError(Exception):
    def __init__(self, message: str, code: str = "TIMESHEET_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


def period_bounds(
    period_type: str, anchor: date
) -> tuple[date, date]:
    """Inclusive start/end for a period containing ``anchor``."""
    if period_type == TimesheetPeriod.DAILY:
        return anchor, anchor
    last = calendar.monthrange(anchor.year, anchor.month)[1]
    return date(anchor.year, anchor.month, 1), date(anchor.year, anchor.month, last)


def _reference_no(intern: Intern, period_type: str, start: date, revision: int) -> str:
    tag = "D" if period_type == TimesheetPeriod.DAILY else "M"
    return f"TS-{tag}{start:%Y%m%d}-{intern.intern_code}-R{revision}"


def _line_item(
    d: date, record: AttendanceRecord | None, resolved
) -> dict:
    """One day of the timesheet, as it will be printed."""
    def hhmm(dt: datetime | None) -> str | None:
        return to_business(dt).strftime("%H:%M") if dt else None

    status = record.status if record else (
        AttendanceStatus.REST_DAY if resolved and resolved.is_rest_day
        else AttendanceStatus.HOLIDAY if resolved and resolved.is_holiday
        else AttendanceStatus.ON_LEAVE if resolved and resolved.is_on_leave
        else AttendanceStatus.NO_SCHEDULE
    )

    return {
        "date": d.isoformat(),
        "day_name": d.strftime("%a"),
        "shift_code": (record.shift_code if record else
                       (resolved.shift_code if resolved else None)),
        "scheduled_start": hhmm(record.scheduled_start if record else
                                (resolved.scheduled_start if resolved else None)),
        "scheduled_end": hhmm(record.scheduled_end if record else
                              (resolved.scheduled_end if resolved else None)),
        "is_overnight": bool(record.is_overnight_shift) if record else (
            resolved.is_overnight if resolved else False
        ),
        "time_in": hhmm(record.time_in) if record else None,
        "time_out": hhmm(record.time_out) if record else None,
        # Flagged so the printed sheet can mark a row that ended the next day.
        "time_out_next_day": bool(
            record and record.time_in and record.time_out
            and to_business(record.time_out).date() != to_business(record.time_in).date()
        ),
        "break_minutes": record.unpaid_break_deducted_minutes if record else 0,
        "scheduled_minutes": record.scheduled_net_minutes if record else (
            resolved.scheduled_net_minutes if resolved else 0
        ),
        "actual_minutes": record.actual_net_minutes if record else 0,
        "actual_hhmm": fmt_hhmm(record.actual_net_minutes) if record else "0:00",
        "creditable_minutes": record.creditable_minutes if record else 0,
        "creditable_hhmm": fmt_hhmm(record.creditable_minutes) if record else "0:00",
        "late_minutes": record.late_minutes if record else 0,
        "undertime_minutes": record.undertime_minutes if record else 0,
        "overtime_minutes": record.approved_overtime_minutes if record else 0,
        "status": status,
        "approval_status": record.approval_status if record else None,
        "remarks": (record.supervisor_remarks or record.remarks) if record else None,
        "attendance_record_id": record.id if record else None,
    }


def generate(
    db: Session,
    intern: Intern,
    *,
    period_type: str = TimesheetPeriod.MONTHLY,
    anchor: date,
    actor_user_id: int | None = None,
    rules: RuleSet | None = None,
    force_new_revision: bool = False,
) -> Timesheet:
    """Build (or rebuild) a timesheet for the period containing ``anchor``.

    A DRAFT or SUBMITTED timesheet is regenerated in place. A SIGNED one is
    never touched: a new revision is created instead and the old row marked
    superseded, so the signed document stays exactly as signed.
    """
    rules = rules or load_rules(db)
    start, end = period_bounds(period_type, anchor)

    existing = list(
        db.scalars(
            select(Timesheet)
            .where(
                Timesheet.intern_id == intern.id,
                Timesheet.period_type == period_type,
                Timesheet.period_start == start,
                Timesheet.is_deleted.is_(False),
            )
            .order_by(Timesheet.revision.desc())
        ).all()
    )
    latest = existing[0] if existing else None

    revision = 1
    target: Timesheet | None = None
    if latest is not None:
        if latest.status in (TimesheetStatus.DRAFT, TimesheetStatus.REJECTED) and not force_new_revision:
            target = latest
            revision = latest.revision
        elif latest.status == TimesheetStatus.SUBMITTED and not force_new_revision:
            target = latest
            revision = latest.revision
        else:
            revision = latest.revision + 1

    # --- Gather the period -------------------------------------------------
    schedule = resolve_range(db, intern, start, end)
    records = db.scalars(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.intern_id == intern.id,
            AttendanceRecord.business_date >= start,
            AttendanceRecord.business_date <= end,
            AttendanceRecord.is_deleted.is_(False),
        )
        .order_by(AttendanceRecord.business_date, AttendanceRecord.session_sequence)
    ).all()
    by_date: dict[date, list[AttendanceRecord]] = {}
    for r in records:
        by_date.setdefault(r.business_date, []).append(r)

    lines: list[dict] = []
    totals = {
        "days_scheduled": 0, "days_present": 0, "days_absent": 0, "days_late": 0,
        "scheduled": 0, "actual": 0, "creditable": 0,
        "late": 0, "undertime": 0, "overtime": 0,
    }

    for d in date_range(start, end):
        resolved = schedule.get(d)
        day_records = by_date.get(d, [])
        if not day_records:
            lines.append(_line_item(d, None, resolved))
            if resolved and resolved.is_working_day:
                totals["days_scheduled"] += 1
            continue

        for rec in day_records:
            lines.append(_line_item(d, rec, resolved))
            totals["scheduled"] += rec.scheduled_net_minutes
            totals["actual"] += rec.actual_net_minutes
            totals["creditable"] += rec.creditable_minutes
            totals["late"] += rec.late_minutes
            totals["undertime"] += rec.undertime_minutes
            totals["overtime"] += rec.approved_overtime_minutes

        first = day_records[0]
        if resolved and resolved.is_working_day:
            totals["days_scheduled"] += 1
        if any(r.status in PRESENT_STATUSES for r in day_records):
            totals["days_present"] += 1
        if first.status == AttendanceStatus.ABSENT:
            totals["days_absent"] += 1
        if any(r.is_late for r in day_records):
            totals["days_late"] += 1

    progress = get_progress(db, intern, as_of=end)

    # --- Persist ------------------------------------------------------------
    if target is None:
        target = Timesheet(
            intern_id=intern.id,
            period_type=period_type,
            period_start=start,
            period_end=end,
            revision=revision,
        )
        db.add(target)
        if latest is not None:
            latest.superseded_by_id = None  # set after flush, needs target.id

    target.reference_no = _reference_no(intern, period_type, start, revision)
    target.period_end = end
    target.total_days_scheduled = totals["days_scheduled"]
    target.total_days_present = totals["days_present"]
    target.total_days_absent = totals["days_absent"]
    target.total_days_late = totals["days_late"]
    target.total_scheduled_minutes = totals["scheduled"]
    target.total_actual_minutes = totals["actual"]
    target.total_creditable_minutes = totals["creditable"]
    target.total_late_minutes = totals["late"]
    target.total_undertime_minutes = totals["undertime"]
    target.total_overtime_minutes = totals["overtime"]
    target.cumulative_approved_minutes = progress.approved_minutes
    target.required_minutes = progress.required_minutes
    target.remaining_minutes = progress.remaining_minutes
    target.line_items = lines
    target.generated_at = utc_now()
    if target.status not in (TimesheetStatus.SUBMITTED,):
        target.status = TimesheetStatus.DRAFT
    db.flush()

    if latest is not None and latest.id != target.id:
        latest.superseded_by_id = target.id
        latest.is_locked = True
        db.flush()

    return target


def content_hash(ts: Timesheet, signer_name: str | None = None) -> str:
    """SHA-256 over the frozen content plus the signer.

    Deterministic: ``sort_keys`` and a fixed field order mean the same content
    always hashes the same, so verification does not depend on dict ordering.
    """
    payload = {
        "reference_no": ts.reference_no,
        "intern_id": ts.intern_id,
        "period_type": ts.period_type,
        "period_start": ts.period_start.isoformat(),
        "period_end": ts.period_end.isoformat(),
        "revision": ts.revision,
        "totals": {
            "days_scheduled": ts.total_days_scheduled,
            "days_present": ts.total_days_present,
            "days_absent": ts.total_days_absent,
            "days_late": ts.total_days_late,
            "scheduled_minutes": ts.total_scheduled_minutes,
            "actual_minutes": ts.total_actual_minutes,
            "creditable_minutes": ts.total_creditable_minutes,
            "late_minutes": ts.total_late_minutes,
            "undertime_minutes": ts.total_undertime_minutes,
            "overtime_minutes": ts.total_overtime_minutes,
            "cumulative_approved_minutes": ts.cumulative_approved_minutes,
            "required_minutes": ts.required_minutes,
            "remaining_minutes": ts.remaining_minutes,
        },
        "line_items": ts.line_items or [],
        "signer": signer_name or ts.signed_by_name,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def submit(
    db: Session, ts: Timesheet, *, actor_user_id: int
) -> Timesheet:
    """Intern submits the timesheet for supervisor review."""
    if ts.status == TimesheetStatus.SIGNED:
        raise TimesheetError(
            "This timesheet is already signed.", code="ALREADY_SIGNED"
        )
    ts.status = TimesheetStatus.SUBMITTED
    ts.submitted_at = utc_now()
    ts.submitted_by_id = actor_user_id
    ts.rejection_reason = None
    db.flush()
    return ts


def sign(
    db: Session,
    ts: Timesheet,
    *,
    signer: User,
    remarks: str | None = None,
    require_all_approved: bool = True,
    lock_records: bool = True,
) -> Timesheet:
    """IT Supervisor signs the timesheet.

    Refuses to sign a sheet containing unapproved attendance, because the
    signature would then certify figures that are still in dispute. Signing
    locks the underlying attendance records so later edits must go through a
    correction request and a new revision.
    """
    from app.core.enums import RoleName

    if ts.status == TimesheetStatus.SIGNED:
        raise TimesheetError("This timesheet is already signed.", code="ALREADY_SIGNED")

    # An intern can never sign their own timesheet.
    intern = db.get(Intern, ts.intern_id)
    if intern and intern.user_id == signer.id:
        raise TimesheetError(
            "An intern cannot sign their own timesheet.", code="SELF_SIGN_FORBIDDEN"
        )
    if not signer.has_role(
        RoleName.IT_SUPERVISOR, RoleName.IT_MANAGER, RoleName.SYSTEM_ADMINISTRATOR
    ):
        raise TimesheetError(
            "Only an IT Supervisor, IT Manager or System Administrator may sign "
            "a timesheet.",
            code="NOT_AUTHORISED_TO_SIGN",
        )

    if require_all_approved:
        pending = db.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.intern_id == ts.intern_id,
                AttendanceRecord.business_date >= ts.period_start,
                AttendanceRecord.business_date <= ts.period_end,
                AttendanceRecord.is_deleted.is_(False),
                AttendanceRecord.time_in.is_not(None),
                AttendanceRecord.approval_status != ApprovalStatus.APPROVED,
            )
        ) or 0
        if pending:
            raise TimesheetError(
                f"{pending} attendance record(s) in this period are not yet "
                f"approved. Approve or reject them before signing.",
                code="UNAPPROVED_RECORDS",
            )

    now = utc_now()
    ts.signed_by_id = signer.id
    ts.signed_by_name = signer.full_name
    ts.signed_by_position = signer.position_title or "IT Supervisor"
    ts.signed_at = now
    ts.signature_remarks = remarks
    ts.status = TimesheetStatus.SIGNED
    ts.is_locked = True
    ts.signature_hash = content_hash(ts, signer.full_name)

    if lock_records:
        for rec in db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.intern_id == ts.intern_id,
                AttendanceRecord.business_date >= ts.period_start,
                AttendanceRecord.business_date <= ts.period_end,
                AttendanceRecord.is_deleted.is_(False),
            )
        ).all():
            rec.is_locked = True

    db.flush()
    return ts


def reject(
    db: Session, ts: Timesheet, *, actor_user_id: int, reason: str
) -> Timesheet:
    if ts.status == TimesheetStatus.SIGNED:
        raise TimesheetError(
            "A signed timesheet cannot be rejected. Generate a new revision "
            "instead.",
            code="ALREADY_SIGNED",
        )
    ts.status = TimesheetStatus.REJECTED
    ts.rejection_reason = reason
    ts.submitted_by_id = ts.submitted_by_id or actor_user_id
    db.flush()
    return ts


def verify_signature(ts: Timesheet) -> dict:
    """Has a signed timesheet been altered since it was signed?"""
    if not ts.is_signed:
        return {"signed": False, "valid": None, "message": "Timesheet is not signed."}
    expected = content_hash(ts, ts.signed_by_name)
    valid = (expected == ts.signature_hash)
    return {
        "signed": True,
        "valid": valid,
        "stored_hash": ts.signature_hash,
        "computed_hash": expected,
        "signed_by": ts.signed_by_name,
        "signed_at": to_business(ts.signed_at).isoformat() if ts.signed_at else None,
        "message": (
            "Signature valid: content matches what was signed."
            if valid else
            "SIGNATURE MISMATCH: the stored content differs from what was signed."
        ),
    }


def to_printable(db: Session, ts: Timesheet, rules: RuleSet | None = None) -> dict:
    """Payload for the print-friendly / PDF layout."""
    rules = rules or load_rules(db)
    intern = db.get(Intern, ts.intern_id)
    return {
        "company_name": rules.str_("company_name"),
        "organization_name": rules.str_("organization_name"),
        "document_title": (
            "DAILY TIME RECORD" if ts.period_type == TimesheetPeriod.DAILY
            else "MONTHLY OJT TIMESHEET"
        ),
        "reference_no": ts.reference_no,
        "revision": ts.revision,
        "intern": {
            "code": intern.intern_code,
            "name": intern.full_name,
            "school": intern.school.name if intern and intern.school else None,
            "course": intern.course,
            "department": intern.department.name if intern and intern.department else None,
            "team": intern.team.name if intern and intern.team else None,
            "supervisor": (
                intern.supervisor.user.full_name
                if intern and intern.supervisor and intern.supervisor.user else None
            ),
        },
        "period": {
            "type": ts.period_type,
            "start": ts.period_start.isoformat(),
            "end": ts.period_end.isoformat(),
        },
        "line_items": ts.line_items or [],
        "totals": {
            "days_scheduled": ts.total_days_scheduled,
            "days_present": ts.total_days_present,
            "days_absent": ts.total_days_absent,
            "days_late": ts.total_days_late,
            "scheduled_hours": minutes_to_hours(ts.total_scheduled_minutes),
            "actual_hours": minutes_to_hours(ts.total_actual_minutes),
            "creditable_hours": minutes_to_hours(ts.total_creditable_minutes),
            "creditable_hhmm": fmt_hhmm(ts.total_creditable_minutes),
            "late_hhmm": fmt_hhmm(ts.total_late_minutes),
            "undertime_hhmm": fmt_hhmm(ts.total_undertime_minutes),
            "overtime_hhmm": fmt_hhmm(ts.total_overtime_minutes),
        },
        "ojt_position": {
            "required_hours": minutes_to_hours(ts.required_minutes),
            "cumulative_approved_hours": minutes_to_hours(ts.cumulative_approved_minutes),
            "remaining_hours": minutes_to_hours(ts.remaining_minutes),
            "completion_percentage": (
                round(min(100.0, ts.cumulative_approved_minutes / ts.required_minutes * 100), 2)
                if ts.required_minutes else 0.0
            ),
        },
        "signature": {
            "status": ts.status,
            "signed_by": ts.signed_by_name,
            "position": ts.signed_by_position,
            "signed_at": to_business(ts.signed_at).isoformat() if ts.signed_at else None,
            "hash": ts.signature_hash,
            "remarks": ts.signature_remarks,
        },
        "verification": verify_signature(ts),
    }
