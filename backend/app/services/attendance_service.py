"""Attendance operations: punches, manual entry, recomputation, finalisation.

All writes that affect OJT hours run inside the caller's transaction and post
to the ledger through ``ojt_engine``, so attendance figures and the hour ledger
can never diverge halfway.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus, AttendanceStatus
from app.core.timeutil import (
    business_date_of, intervals_overlap, minutes_between, to_business, to_utc, utc_now,
)
from app.models.attendance import AttendanceBreak, AttendanceRecord
from app.models.intern import Intern
from app.services.attendance_engine import DayContext, compute
from app.services.schedule_resolver import (
    ResolvedSchedule, find_shift_for_timestamp, resolve_for_date,
)
from app.services.settings_service import RuleSet, load_rules


class AttendanceError(Exception):
    """A rejected attendance operation, with a message safe to show the user."""

    def __init__(self, message: str, code: str = "ATTENDANCE_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def context_from_resolved(
    resolved: ResolvedSchedule | None,
    intern: Intern,
    *,
    evaluation_time: datetime | None = None,
    leave_is_creditable: bool = False,
    leave_credited_minutes: int = 0,
) -> DayContext:
    if resolved is None:
        return DayContext(
            has_schedule=False,
            evaluation_time=evaluation_time,
            max_creditable_minutes_per_day=intern.max_creditable_minutes_per_day,
        )
    return DayContext(
        is_rest_day=resolved.is_rest_day,
        is_holiday=resolved.is_holiday,
        holiday_is_non_working=resolved.holiday_is_non_working,
        is_on_leave=resolved.is_on_leave,
        leave_is_official_business=resolved.leave_is_official_business,
        leave_is_creditable=leave_is_creditable,
        leave_credited_minutes=leave_credited_minutes,
        has_schedule=resolved.has_shift,
        max_creditable_minutes_per_day=intern.max_creditable_minutes_per_day,
        evaluation_time=evaluation_time,
    )


def _apply_schedule_snapshot(record: AttendanceRecord, resolved: ResolvedSchedule | None) -> None:
    """Freeze the shift configuration onto the record.

    Written once, at creation. Later edits to the ShiftTemplate do not touch
    existing records, which is what keeps historical computations stable.
    """
    if resolved is None or not resolved.has_shift:
        record.shift_template_id = None
        record.shift_code = None
        record.scheduled_start = None
        record.scheduled_end = None
        record.is_overnight_shift = False
        record.shift_snapshot = None
        return
    record.shift_template_id = resolved.shift_template_id
    record.shift_code = resolved.shift_code
    record.scheduled_start = resolved.scheduled_start
    record.scheduled_end = resolved.scheduled_end
    record.is_overnight_shift = resolved.is_overnight
    record.shift_snapshot = resolved.shift_snapshot


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_open_session(db: Session, intern_id: int) -> AttendanceRecord | None:
    """The intern's currently open session, if any (timed in, not out)."""
    return db.scalar(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.intern_id == intern_id,
            AttendanceRecord.is_deleted.is_(False),
            AttendanceRecord.time_in.is_not(None),
            AttendanceRecord.time_out.is_(None),
        )
        .order_by(AttendanceRecord.time_in.desc())
    )


def get_records_for_date(
    db: Session, intern_id: int, business_date: date
) -> list[AttendanceRecord]:
    return list(
        db.scalars(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.intern_id == intern_id,
                AttendanceRecord.business_date == business_date,
                AttendanceRecord.is_deleted.is_(False),
            )
            .order_by(AttendanceRecord.session_sequence)
        ).all()
    )


def _next_sequence(db: Session, intern_id: int, business_date: date) -> int:
    existing = get_records_for_date(db, intern_id, business_date)
    return max((r.session_sequence for r in existing), default=0) + 1


def _assert_no_overlap(
    db: Session,
    intern_id: int,
    start: datetime,
    end: datetime | None,
    *,
    exclude_record_id: int | None = None,
    window_days: int = 3,
) -> None:
    """Reject a session that would overlap an existing one.

    The window spans a few days either side because an overnight session can
    overlap a neighbouring business date's record.
    """
    probe_end = end or start
    lo = (to_business(start) - timedelta(days=window_days)).date()
    hi = (to_business(probe_end) + timedelta(days=window_days)).date()

    stmt = select(AttendanceRecord).where(
        AttendanceRecord.intern_id == intern_id,
        AttendanceRecord.is_deleted.is_(False),
        AttendanceRecord.business_date >= lo,
        AttendanceRecord.business_date <= hi,
        AttendanceRecord.time_in.is_not(None),
    )
    if exclude_record_id:
        stmt = stmt.where(AttendanceRecord.id != exclude_record_id)

    for other in db.scalars(stmt).all():
        o_start = other.time_in
        o_end = other.time_out or other.scheduled_end or other.time_in
        if intervals_overlap(start, probe_end if end else start + timedelta(minutes=1),
                             o_start, o_end):
            raise AttendanceError(
                f"This session overlaps an existing attendance session on "
                f"{other.business_date} ({other.shift_code or 'no shift'}). "
                f"Overlapping attendance sessions are not permitted.",
                code="OVERLAPPING_ATTENDANCE",
            )


# ---------------------------------------------------------------------------
# Digital timekeeping
# ---------------------------------------------------------------------------

def time_in(
    db: Session,
    intern: Intern,
    *,
    timestamp: datetime | None = None,
    actor_user_id: int | None = None,
    ip_address: str | None = None,
    rules: RuleSet | None = None,
    source: str = "DIGITAL_TIMEKEEPING",
    allow_rest_day: bool = True,
) -> AttendanceRecord:
    """Record a time-in, binding it to the correct scheduled shift.

    Duplicate time-in is rejected: an intern with an open session must time out
    first. The business date comes from the matched shift's scheduled start,
    which is what keeps an overnight session on one record.
    """
    rules = rules or load_rules(db)
    ts = to_utc(timestamp or utc_now())

    existing_open = get_open_session(db, intern.id)
    if existing_open is not None:
        raise AttendanceError(
            f"Already timed in at "
            f"{to_business(existing_open.time_in).strftime('%Y-%m-%d %H:%M')} "
            f"for business date {existing_open.business_date}. "
            f"Time out before timing in again.",
            code="DUPLICATE_TIME_IN",
        )

    resolved = find_shift_for_timestamp(
        db, intern, ts,
        early_window_minutes=rules.int_("early_time_in_window_minutes"),
        late_window_minutes=rules.int_("late_time_in_window_minutes"),
    )

    if resolved is None:
        if not rules.bool_("allow_attendance_without_schedule"):
            raise AttendanceError(
                "No shift is assigned around this time, and attendance without "
                "a schedule is disabled.",
                code="NO_SCHEDULE",
            )
        business_date = business_date_of(ts)
        resolved = resolve_for_date(db, intern, business_date)
    else:
        business_date = resolved.business_date

    if resolved.is_rest_day and not allow_rest_day:
        raise AttendanceError(
            f"{business_date} is a rest day for this intern.", code="REST_DAY"
        )

    _assert_no_overlap(db, intern.id, ts, None)

    record = AttendanceRecord(
        intern_id=intern.id,
        business_date=business_date,
        session_sequence=_next_sequence(db, intern.id, business_date),
        time_in=ts,
        time_out=None,
        source=source,
        time_in_ip=ip_address,
        approval_status=ApprovalStatus.PENDING,
        created_by_id=actor_user_id,
    )
    _apply_schedule_snapshot(record, resolved)
    record.breaks = []
    compute(record, rules, context_from_resolved(resolved, intern, evaluation_time=ts))
    db.add(record)
    db.flush()
    return record


def time_out(
    db: Session,
    intern: Intern,
    *,
    timestamp: datetime | None = None,
    actor_user_id: int | None = None,
    ip_address: str | None = None,
    rules: RuleSet | None = None,
) -> AttendanceRecord:
    """Close the intern's open session."""
    rules = rules or load_rules(db)
    ts = to_utc(timestamp or utc_now())

    record = get_open_session(db, intern.id)
    if record is None:
        raise AttendanceError(
            "No open attendance session to close. Time in first.",
            code="NO_OPEN_SESSION",
        )

    if ts <= record.time_in:
        raise AttendanceError(
            "Time out must be later than time in.", code="INVALID_TIME_OUT"
        )

    span = minutes_between(record.time_in, ts)
    if span > rules.int_("max_session_minutes"):
        raise AttendanceError(
            f"This session would be {span // 60}h {span % 60}m long, beyond the "
            f"{rules.int_('max_session_minutes') // 60}h limit. A supervisor must "
            f"record this manually.",
            code="UNUSUAL_WORKING_HOURS",
        )

    # Close any break left open.
    for b in record.breaks:
        if b.break_end is None:
            b.break_end = min(ts, b.break_start + timedelta(hours=4))
            b.duration_minutes = max(0, minutes_between(b.break_start, b.break_end))

    record.time_out = ts
    record.time_out_ip = ip_address
    record.updated_by_id = actor_user_id

    resolved = resolve_for_date(db, intern, record.business_date)
    compute(record, rules, context_from_resolved(resolved, intern, evaluation_time=ts))

    # Post to the hour ledger here rather than relying on the caller.
    # Closing a session is the moment its creditable minutes become known, and
    # a caller that forgets to post would leave the intern's hours silently
    # missing. Posting is idempotent, so the API layer calling it again is a
    # no-op, and the entry lands unapproved until a supervisor approves it.
    from app.services import ojt_engine

    ojt_engine.post_attendance(db, record, actor_user_id=actor_user_id, rules=rules)

    db.flush()
    return record


def break_start(
    db: Session,
    intern: Intern,
    *,
    timestamp: datetime | None = None,
    break_type: str = "LUNCH",
    is_paid: bool = False,
    rules: RuleSet | None = None,
) -> AttendanceBreak:
    rules = rules or load_rules(db)
    ts = to_utc(timestamp or utc_now())
    record = get_open_session(db, intern.id)
    if record is None:
        raise AttendanceError(
            "No open attendance session. Time in before starting a break.",
            code="NO_OPEN_SESSION",
        )
    for b in record.breaks:
        if b.break_end is None:
            raise AttendanceError(
                "A break is already in progress.", code="BREAK_ALREADY_OPEN"
            )
    if ts < record.time_in:
        raise AttendanceError(
            "Break cannot start before time in.", code="INVALID_BREAK_START"
        )

    brk = AttendanceBreak(
        attendance_record_id=record.id,
        break_start=ts,
        break_type=break_type,
        is_paid=is_paid,
    )
    db.add(brk)
    record.breaks.append(brk)
    db.flush()
    return brk


def break_end(
    db: Session,
    intern: Intern,
    *,
    timestamp: datetime | None = None,
    rules: RuleSet | None = None,
) -> AttendanceBreak:
    rules = rules or load_rules(db)
    ts = to_utc(timestamp or utc_now())
    record = get_open_session(db, intern.id)
    if record is None:
        raise AttendanceError("No open attendance session.", code="NO_OPEN_SESSION")

    open_break = next((b for b in record.breaks if b.break_end is None), None)
    if open_break is None:
        raise AttendanceError("No break in progress.", code="NO_OPEN_BREAK")
    if ts <= open_break.break_start:
        raise AttendanceError(
            "Break end must be later than break start.", code="INVALID_BREAK_END"
        )

    open_break.break_end = ts
    open_break.duration_minutes = minutes_between(open_break.break_start, ts)
    db.flush()
    return open_break


# ---------------------------------------------------------------------------
# Manual entry and recomputation
# ---------------------------------------------------------------------------

def manual_entry(
    db: Session,
    intern: Intern,
    business_date: date,
    *,
    time_in_at: datetime | None,
    time_out_at: datetime | None,
    actor_user_id: int,
    status_override: str | None = None,
    remarks: str | None = None,
    overtime_approved: bool = False,
    rules: RuleSet | None = None,
    session_sequence: int | None = None,
) -> AttendanceRecord:
    """Supervisor-recorded attendance for a business date.

    Used for corrections and for interns who could not use digital timekeeping.
    The business date is given explicitly rather than inferred, because a
    supervisor fixing an overnight shift must be able to state which shift the
    session belongs to.
    """
    rules = rules or load_rules(db)
    if time_in_at and time_out_at and to_utc(time_out_at) <= to_utc(time_in_at):
        raise AttendanceError(
            "Time out must be later than time in.", code="INVALID_TIME_RANGE"
        )

    resolved = resolve_for_date(db, intern, business_date)

    existing = get_records_for_date(db, intern.id, business_date)
    record: AttendanceRecord | None = None
    if session_sequence is not None:
        record = next((r for r in existing if r.session_sequence == session_sequence), None)
    elif len(existing) == 1:
        record = existing[0]

    if record is None:
        record = AttendanceRecord(
            intern_id=intern.id,
            business_date=business_date,
            session_sequence=session_sequence or _next_sequence(db, intern.id, business_date),
            created_by_id=actor_user_id,
        )
        _apply_schedule_snapshot(record, resolved)
        db.add(record)
        record.breaks = []
    else:
        if record.is_locked:
            raise AttendanceError(
                "This record is locked by a signed timesheet. Create a correction "
                "request instead.",
                code="RECORD_LOCKED",
            )
        # Keep the original snapshot: that is the whole point of snapshotting.
        if record.shift_snapshot is None:
            _apply_schedule_snapshot(record, resolved)

    if time_in_at is not None:
        _assert_no_overlap(
            db, intern.id, to_utc(time_in_at),
            to_utc(time_out_at) if time_out_at else None,
            exclude_record_id=record.id,
        )

    record.time_in = to_utc(time_in_at) if time_in_at else None
    record.time_out = to_utc(time_out_at) if time_out_at else None
    record.source = "MANUAL_ENTRY"
    record.overtime_approved = overtime_approved
    if overtime_approved:
        record.overtime_approved_by_id = actor_user_id
    record.updated_by_id = actor_user_id
    if remarks:
        record.supervisor_remarks = remarks

    compute(record, rules, context_from_resolved(resolved, intern))

    if status_override:
        record.status = status_override

    db.flush()
    return record


def recompute_record(
    db: Session,
    record: AttendanceRecord,
    *,
    rules: RuleSet | None = None,
    evaluation_time: datetime | None = None,
) -> AttendanceRecord:
    """Recompute a record from its stored punches and frozen snapshot."""
    rules = rules or load_rules(db)
    intern = record.intern or db.get(Intern, record.intern_id)
    resolved = resolve_for_date(db, intern, record.business_date)
    compute(
        record, rules,
        context_from_resolved(resolved, intern, evaluation_time=evaluation_time),
    )
    db.flush()
    return record


def ensure_day_records(
    db: Session,
    intern: Intern,
    business_date: date,
    *,
    rules: RuleSet | None = None,
    evaluation_time: datetime | None = None,
) -> AttendanceRecord | None:
    """Materialise the record for a scheduled day that has no punches yet.

    Needed so the calendar, duty board and reports can show REST_DAY, HOLIDAY,
    ON_LEAVE and ABSENT days rather than silent gaps. Returns None when the day
    has no schedule and no reason to exist.
    """
    rules = rules or load_rules(db)
    existing = get_records_for_date(db, intern.id, business_date)
    if existing:
        return existing[0]

    resolved = resolve_for_date(db, intern, business_date)
    if not (resolved.has_shift or resolved.is_rest_day or resolved.is_holiday
            or resolved.is_on_leave):
        return None

    record = AttendanceRecord(
        intern_id=intern.id,
        business_date=business_date,
        session_sequence=1,
        source="SYSTEM_GENERATED",
        approval_status=ApprovalStatus.PENDING,
    )
    _apply_schedule_snapshot(record, resolved)
    record.breaks = []
    compute(
        record, rules,
        context_from_resolved(resolved, intern, evaluation_time=evaluation_time),
    )
    db.add(record)
    db.flush()
    return record


def finalize_absences(
    db: Session,
    business_date: date,
    *,
    rules: RuleSet | None = None,
    evaluation_time: datetime | None = None,
    intern_ids: list[int] | None = None,
) -> dict[str, int]:
    """Close out a business date.

    Marks ABSENT only where the shift has ended AND the absence grace period
    has elapsed, and flags still-open sessions as INCOMPLETE_TIME_RECORD. Safe
    to run repeatedly; it never marks an intern absent early.
    """
    from app.core.enums import InternStatus

    rules = rules or load_rules(db)
    now = evaluation_time or utc_now()

    stmt = select(Intern).where(
        Intern.is_deleted.is_(False),
        Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]),
    )
    if intern_ids:
        stmt = stmt.where(Intern.id.in_(intern_ids))

    counts = {"absent": 0, "incomplete": 0, "created": 0, "unchanged": 0}

    for intern in db.scalars(stmt).all():
        record = ensure_day_records(
            db, intern, business_date, rules=rules, evaluation_time=now
        )
        if record is None:
            continue
        if record.created_at is None:
            counts["created"] += 1

        before = record.status
        recompute_record(db, record, rules=rules, evaluation_time=now)
        if record.status == AttendanceStatus.ABSENT and before != AttendanceStatus.ABSENT:
            counts["absent"] += 1
        elif record.status == AttendanceStatus.INCOMPLETE_TIME_RECORD:
            counts["incomplete"] += 1
        else:
            counts["unchanged"] += 1

    db.flush()
    return counts
