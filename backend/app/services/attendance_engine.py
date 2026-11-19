"""Pure attendance computation.

This module contains no database writes and no I/O: it takes an attendance
record (with its shift snapshot and breaks) plus a context describing the day,
and fills in the computed minute figures and the status. That separation is
deliberate - it is what makes the 20 mandated scenarios testable without a
database, and it means recomputation is always reproducible from stored data.

The figures the spec requires to be kept SEPARATE are kept separate:

    scheduled_elapsed_minutes   9h for a standard shift
    scheduled_break_minutes     unpaid portion only
    scheduled_net_minutes       expected working time (8h for a 9h shift)
    actual_elapsed_minutes      time_out - time_in, raw
    actual_break_minutes        logged break time
    actual_net_minutes          what the intern actually rendered
    creditable_minutes          what counts toward OJT, capped and clipped
    approved_overtime_minutes   authorised overtime only

``actual_net_minutes`` is never overwritten by the capped creditable figure.
An intern who renders 10 hours on a day capped at 8 keeps the 10 on record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.enums import AttendanceStatus
from app.core.timeutil import minutes_between, overlap_minutes, utc_now
from app.models.attendance import AttendanceRecord
from app.services.settings_service import RuleSet


@dataclass
class DayContext:
    """Everything about the day that is not on the record itself."""

    is_rest_day: bool = False
    is_holiday: bool = False
    holiday_is_non_working: bool = True
    holiday_is_creditable: bool = False
    is_on_leave: bool = False
    leave_is_official_business: bool = False
    leave_is_creditable: bool = False
    leave_credited_minutes: int = 0
    has_schedule: bool = True
    # Per-intern override of the daily credit ceiling; None inherits the rule.
    max_creditable_minutes_per_day: int | None = None
    # "Now", for deciding whether a shift has ended yet. Injected so tests are
    # deterministic and so a batch job can evaluate a past day correctly.
    evaluation_time: datetime | None = None

    @property
    def now(self) -> datetime:
        return self.evaluation_time or utc_now()


@dataclass
class BreakInterval:
    start: datetime
    end: datetime | None
    is_paid: bool = False

    @property
    def is_closed(self) -> bool:
        return self.end is not None


def _break_intervals(record: AttendanceRecord) -> list[BreakInterval]:
    return [
        BreakInterval(start=b.break_start, end=b.break_end, is_paid=b.is_paid)
        for b in record.breaks
        if not b.is_deleted
    ]


def _clipped_break_minutes(
    breaks: list[BreakInterval],
    window_start: datetime,
    window_end: datetime,
    *,
    unpaid_only: bool,
) -> int:
    """Break minutes falling inside a window.

    Clipping matters: a break that starts before time-out and runs past it must
    not deduct more than the time actually inside the session, or a sloppy punch
    could drive rendered hours negative.
    """
    total = 0
    for b in breaks:
        if not b.is_closed:
            continue
        if unpaid_only and b.is_paid:
            continue
        total += overlap_minutes(b.start, b.end, window_start, window_end)
    return total


def compute(
    record: AttendanceRecord,
    rules: RuleSet,
    context: DayContext | None = None,
) -> AttendanceRecord:
    """Fill in every computed field on ``record``. Mutates and returns it."""
    ctx = context or DayContext()
    snap = record.shift_snapshot or {}

    # --- Scheduled figures (from the immutable snapshot) --------------------
    if record.scheduled_start and record.scheduled_end:
        record.scheduled_elapsed_minutes = int(
            snap.get("elapsed_minutes")
            or minutes_between(record.scheduled_start, record.scheduled_end)
        )
        record.scheduled_break_minutes = int(snap.get("unpaid_break_minutes", 0))
        record.scheduled_net_minutes = int(
            snap.get("net_working_minutes",
                     record.scheduled_elapsed_minutes - record.scheduled_break_minutes)
        )
    else:
        record.scheduled_elapsed_minutes = 0
        record.scheduled_break_minutes = 0
        record.scheduled_net_minutes = 0

    grace = int(snap.get("grace_period_minutes", rules.int_("default_grace_period_minutes")))
    absence_grace = int(
        snap.get("absence_grace_minutes", rules.int_("absence_finalize_grace_minutes"))
    )
    max_ot = int(snap.get("max_overtime_minutes", 0))

    breaks = _break_intervals(record)

    # --- No punches at all --------------------------------------------------
    if record.time_in is None:
        _reset_actuals(record)
        record.status = _status_without_punches(record, ctx, absence_grace)
        record.creditable_minutes = _non_working_credit(record, ctx, rules)
        return record

    # --- Open session (timed in, not out) -----------------------------------
    if record.time_out is None:
        _compute_late(record, grace, rules)
        record.actual_elapsed_minutes = 0
        record.actual_break_minutes = _clipped_break_minutes(
            breaks, record.time_in, ctx.now, unpaid_only=False
        )
        record.unpaid_break_deducted_minutes = 0
        record.actual_net_minutes = 0
        record.undertime_minutes = 0
        record.overtime_minutes = 0
        record.approved_overtime_minutes = 0
        record.creditable_minutes = 0
        # A session is only INCOMPLETE once the shift has had time to finish.
        shift_over = (
            record.scheduled_end is not None
            and ctx.now > record.scheduled_end + timedelta(minutes=absence_grace)
        )
        record.status = (
            AttendanceStatus.INCOMPLETE_TIME_RECORD if shift_over
            else AttendanceStatus.IN_PROGRESS
        )
        return record

    # --- Complete session ---------------------------------------------------
    t_in, t_out = record.time_in, record.time_out
    record.actual_elapsed_minutes = max(0, minutes_between(t_in, t_out))

    # Breaks, clipped to the session.
    record.actual_break_minutes = _clipped_break_minutes(
        breaks, t_in, t_out, unpaid_only=False
    )
    logged_unpaid = _clipped_break_minutes(breaks, t_in, t_out, unpaid_only=True)

    # Unpaid break deduction policy.
    scheduled_unpaid = record.scheduled_break_minutes
    if logged_unpaid > 0:
        if rules.bool_("deduct_actual_break_when_longer"):
            deduction = max(logged_unpaid, scheduled_unpaid)
        else:
            deduction = scheduled_unpaid
    elif rules.bool_("auto_deduct_unpaid_break"):
        # Nobody logged a break; the shift's unpaid break is still unpaid.
        deduction = scheduled_unpaid
    else:
        deduction = 0

    deduction = min(deduction, record.actual_elapsed_minutes)
    deduction = min(deduction, rules.int_("max_break_minutes_per_session"))
    record.unpaid_break_deducted_minutes = deduction
    record.actual_net_minutes = max(0, record.actual_elapsed_minutes - deduction)

    # --- Credit inside the scheduled window --------------------------------
    if record.scheduled_start and record.scheduled_end:
        in_sched = overlap_minutes(t_in, t_out, record.scheduled_start, record.scheduled_end)
        unpaid_in_sched = _clipped_break_minutes(
            breaks,
            max(t_in, record.scheduled_start),
            min(t_out, record.scheduled_end),
            unpaid_only=True,
        )
        if unpaid_in_sched == 0 and logged_unpaid == 0 and rules.bool_("auto_deduct_unpaid_break"):
            unpaid_in_sched = min(scheduled_unpaid, in_sched)
        elif logged_unpaid > 0 and rules.bool_("deduct_actual_break_when_longer"):
            unpaid_in_sched = max(unpaid_in_sched, min(scheduled_unpaid, in_sched))

        base_credit = max(0, in_sched - unpaid_in_sched)
        base_credit = min(base_credit, record.scheduled_net_minutes)

        # Work outside the scheduled window is overtime, not base credit.
        before = overlap_minutes(
            t_in, t_out, t_in, record.scheduled_start
        ) if t_in < record.scheduled_start else 0
        after = overlap_minutes(
            t_in, t_out, record.scheduled_end, t_out
        ) if t_out > record.scheduled_end else 0

        if rules.bool_("credit_work_before_shift_start"):
            base_credit += before
            before = 0

        record.overtime_minutes = before + after

        # Undertime, with the grace period honoured. An arrival inside the
        # grace window is treated as on time, so the minutes it accounts for
        # are not also reported as undertime - otherwise using the grace
        # period would always produce an UNDERTIME exception.
        forgiven = 0
        if rules.bool_("grace_forgives_undertime") and record.time_in is not None:
            raw_late = max(0, minutes_between(record.scheduled_start, record.time_in))
            if raw_late <= grace:
                forgiven = raw_late

        record.undertime_minutes = max(
            0, record.scheduled_net_minutes - base_credit - forgiven
        )
        if record.undertime_minutes < rules.int_("undertime_tolerance_minutes"):
            record.undertime_minutes = 0
    else:
        # NO_SCHEDULE: nothing to clip against, nothing scheduled to owe.
        base_credit = record.actual_net_minutes
        record.overtime_minutes = 0
        record.undertime_minutes = 0

    # --- Overtime authorisation --------------------------------------------
    if record.overtime_approved and rules.bool_("count_overtime_toward_ojt"):
        allowed = record.overtime_minutes
        if max_ot > 0:
            allowed = min(allowed, max_ot)
        record.approved_overtime_minutes = allowed
    elif rules.bool_("count_unapproved_overtime"):
        record.approved_overtime_minutes = record.overtime_minutes
    else:
        record.approved_overtime_minutes = 0

    creditable = base_credit + record.approved_overtime_minutes

    # --- Daily ceiling ------------------------------------------------------
    cap = ctx.max_creditable_minutes_per_day
    if cap is None:
        cap = rules.int_("max_creditable_minutes_per_day")
    record.credit_capped = False
    record.cap_reason = None
    if cap is not None and creditable > cap:
        creditable = cap
        record.credit_capped = True
        record.cap_reason = f"Daily ceiling of {cap} minutes applied"

    if creditable < rules.int_("min_creditable_minutes"):
        creditable = 0

    record.creditable_minutes = max(0, creditable)

    # --- Late ---------------------------------------------------------------
    _compute_late(record, grace, rules)

    # --- Status -------------------------------------------------------------
    record.status = _status_with_punches(record, ctx, rules)
    return record


def _reset_actuals(record: AttendanceRecord) -> None:
    record.actual_elapsed_minutes = 0
    record.actual_break_minutes = 0
    record.unpaid_break_deducted_minutes = 0
    record.actual_net_minutes = 0
    record.late_minutes = 0
    record.is_late = False
    record.undertime_minutes = 0
    record.overtime_minutes = 0
    record.approved_overtime_minutes = 0
    record.creditable_minutes = 0
    record.credit_capped = False
    record.cap_reason = None


def _compute_late(record: AttendanceRecord, grace: int, rules: RuleSet) -> None:
    if record.time_in is None or record.scheduled_start is None:
        record.late_minutes = 0
        record.is_late = False
        return
    raw = max(0, minutes_between(record.scheduled_start, record.time_in))
    record.is_late = raw > grace
    if rules.bool_("grace_reduces_late_minutes"):
        record.late_minutes = max(0, raw - grace)
    else:
        record.late_minutes = raw if record.is_late else 0


def _non_working_credit(record: AttendanceRecord, ctx: DayContext, rules: RuleSet) -> int:
    """Credit for a day with no punches - leave or holiday, if policy allows."""
    if ctx.is_on_leave:
        allowed = ctx.leave_is_creditable or rules.bool_("approved_leave_counts_as_hours")
        if allowed:
            return ctx.leave_credited_minutes or record.scheduled_net_minutes
        return 0
    if ctx.is_holiday and (ctx.holiday_is_creditable or rules.bool_("holiday_counts_as_hours")):
        return record.scheduled_net_minutes
    return 0


def _status_without_punches(
    record: AttendanceRecord, ctx: DayContext, absence_grace: int
) -> str:
    """Status for a day with no time-in.

    The ordering encodes the spec's rule that an intern is never marked absent
    before the shift has ended and the grace period has elapsed.
    """
    if ctx.is_on_leave:
        return (
            AttendanceStatus.OFFICIAL_BUSINESS if ctx.leave_is_official_business
            else AttendanceStatus.ON_LEAVE
        )
    if ctx.is_rest_day:
        return AttendanceStatus.REST_DAY
    if ctx.is_holiday and ctx.holiday_is_non_working:
        return AttendanceStatus.HOLIDAY
    if not ctx.has_schedule or record.scheduled_end is None:
        return AttendanceStatus.NO_SCHEDULE

    deadline = record.scheduled_end + timedelta(minutes=absence_grace)
    if ctx.now <= deadline:
        # Shift has not finished, or grace has not run out: still pending.
        return AttendanceStatus.SCHEDULED
    return AttendanceStatus.ABSENT


def _status_with_punches(record: AttendanceRecord, ctx: DayContext, rules: RuleSet) -> str:
    """Status for a completed session.

    Worked time wins over the day type: an intern who actually reported on a
    rest day or holiday is PRESENT (with the hours as overtime), not REST_DAY.
    """
    if record.scheduled_start is None:
        return AttendanceStatus.NO_SCHEDULE
    if ctx.is_on_leave and record.actual_net_minutes == 0:
        return AttendanceStatus.ON_LEAVE

    if record.scheduled_net_minutes > 0:
        ratio = record.actual_net_minutes / record.scheduled_net_minutes
        if ratio <= rules.float_("half_day_threshold_ratio"):
            return AttendanceStatus.HALF_DAY

    if record.is_late:
        return AttendanceStatus.LATE
    if record.undertime_minutes > 0:
        return AttendanceStatus.UNDERTIME
    return AttendanceStatus.PRESENT
