"""Resolve which shift an intern is scheduled for on a given date.

Precedence, highest first:

    DAILY_OVERRIDE  (a specific date)
    TEMPORARY       (a bounded date range)
    ROTATING        (week index within a cycle)
    WEEKLY_RECURRING(a plain weekday pattern)

Rest days and holidays are layered on top. Nothing here assumes a
Monday-to-Friday week: every intern's working days and rest days are resolved
independently from their own assignment rows.

The other job of this module is BUSINESS-DATE BINDING. For an overnight shift
the business date is the shift's scheduled START date, so a session timed in at
21:55 on Nov 20 and out at 07:05 on Nov 21 is one record dated Nov 20.
``find_shift_for_timestamp`` is what decides that, by testing both the
timestamp's own date and the previous date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus
from app.core.timeutil import shift_window, to_business
from app.models.intern import Intern
from app.models.leave import LeaveRequest
from app.models.schedule import Holiday, RestDay, ScheduleAssignment, ShiftTemplate


@dataclass
class ResolvedSchedule:
    """The schedule in force for one intern on one business date."""

    business_date: date
    intern_id: int

    shift_template_id: int | None = None
    shift_code: str | None = None
    shift_name: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    is_overnight: bool = False

    scheduled_elapsed_minutes: int = 0
    scheduled_break_minutes: int = 0
    scheduled_net_minutes: int = 0

    grace_period_minutes: int = 0
    absence_grace_minutes: int = 0
    max_overtime_minutes: int = 0

    is_rest_day: bool = False
    is_holiday: bool = False
    holiday_name: str | None = None
    holiday_is_non_working: bool = True
    is_on_leave: bool = False
    leave_is_official_business: bool = False
    leave_request_id: int | None = None

    assignment_id: int | None = None
    assignment_type: str | None = None
    shift_snapshot: dict | None = None
    source: str = "NONE"  # ASSIGNMENT | REST_DAY | HOLIDAY | NONE

    @property
    def has_shift(self) -> bool:
        return self.shift_template_id is not None and self.scheduled_start is not None

    @property
    def is_working_day(self) -> bool:
        """Expected to report: has a shift, not resting, not on leave."""
        if not self.has_shift or self.is_rest_day or self.is_on_leave:
            return False
        if self.is_holiday and self.holiday_is_non_working:
            return False
        return True

    def as_dict(self) -> dict:
        return {
            "business_date": self.business_date.isoformat(),
            "intern_id": self.intern_id,
            "shift_code": self.shift_code,
            "shift_name": self.shift_name,
            "scheduled_start": self.scheduled_start.isoformat() if self.scheduled_start else None,
            "scheduled_end": self.scheduled_end.isoformat() if self.scheduled_end else None,
            "is_overnight": self.is_overnight,
            "scheduled_elapsed_minutes": self.scheduled_elapsed_minutes,
            "scheduled_break_minutes": self.scheduled_break_minutes,
            "scheduled_net_minutes": self.scheduled_net_minutes,
            "is_rest_day": self.is_rest_day,
            "is_holiday": self.is_holiday,
            "holiday_name": self.holiday_name,
            "is_on_leave": self.is_on_leave,
            "is_working_day": self.is_working_day,
            "assignment_type": self.assignment_type,
            "source": self.source,
        }


def _apply_template(res: ResolvedSchedule, t: ShiftTemplate, d: date) -> None:
    start_dt, end_dt = shift_window(d, t.start_time, t.end_time)
    res.shift_template_id = t.id
    res.shift_code = t.code
    res.shift_name = t.name
    res.scheduled_start = start_dt
    res.scheduled_end = end_dt
    res.is_overnight = t.is_overnight
    res.scheduled_elapsed_minutes = t.elapsed_minutes
    res.scheduled_break_minutes = t.unpaid_break_minutes
    res.scheduled_net_minutes = t.net_working_minutes
    res.grace_period_minutes = t.grace_period_minutes
    res.absence_grace_minutes = t.absence_grace_minutes
    res.max_overtime_minutes = t.max_overtime_minutes
    res.shift_snapshot = t.snapshot()


def _load_assignments(
    db: Session, intern_id: int, start: date, end: date, exclude_assignment_id: int | None
) -> list[ScheduleAssignment]:
    stmt = (
        select(ScheduleAssignment)
        .where(
            ScheduleAssignment.intern_id == intern_id,
            ScheduleAssignment.is_deleted.is_(False),
            ScheduleAssignment.approval_status == ApprovalStatus.APPROVED,
            ScheduleAssignment.effective_from <= end,
        )
        .order_by(ScheduleAssignment.effective_from.desc(), ScheduleAssignment.id.desc())
    )
    rows = [
        a for a in db.scalars(stmt).all()
        if a.effective_to is None or a.effective_to >= start
    ]
    if exclude_assignment_id:
        rows = [a for a in rows if a.id != exclude_assignment_id]
    return rows


def _pick_assignment(
    assignments: list[ScheduleAssignment], d: date
) -> ScheduleAssignment | None:
    """Highest-precedence assignment covering ``d``.

    Ties are broken by the later effective_from, then the higher id, so the
    most recently created assignment wins a genuine tie.
    """
    candidates = [a for a in assignments if a.covers_date(d)]
    if not candidates:
        return None
    return max(candidates, key=lambda a: (a.precedence, a.effective_from, a.id))


def resolve_for_date(
    db: Session,
    intern: Intern,
    d: date,
    *,
    templates: dict[int, ShiftTemplate] | None = None,
    assignments: list[ScheduleAssignment] | None = None,
    rest_days: list[RestDay] | None = None,
    holidays: dict[date, Holiday] | None = None,
    leaves: list[LeaveRequest] | None = None,
    exclude_assignment_id: int | None = None,
) -> ResolvedSchedule:
    """Resolve one intern / one date.

    The optional pre-loaded collections let callers resolve a whole month
    without a query per day; passing none of them makes this self-contained.
    """
    res = ResolvedSchedule(business_date=d, intern_id=intern.id)

    if assignments is None:
        assignments = _load_assignments(db, intern.id, d, d, exclude_assignment_id)

    # --- Holiday -----------------------------------------------------------
    if holidays is None:
        hol = db.scalar(
            select(Holiday).where(
                Holiday.holiday_date == d,
                Holiday.is_deleted.is_(False),
                Holiday.department_id.in_([intern.department_id, None])
                if intern.department_id
                else Holiday.department_id.is_(None),
            )
        )
    else:
        hol = holidays.get(d)
    if hol is not None:
        res.is_holiday = True
        res.holiday_name = hol.name
        res.holiday_is_non_working = hol.is_non_working

    # --- Approved leave ----------------------------------------------------
    if leaves is None:
        leaves = list(
            db.scalars(
                select(LeaveRequest).where(
                    LeaveRequest.intern_id == intern.id,
                    LeaveRequest.is_deleted.is_(False),
                    LeaveRequest.approval_status == ApprovalStatus.APPROVED,
                    LeaveRequest.date_from <= d,
                    LeaveRequest.date_to >= d,
                )
            ).all()
        )
    for lv in leaves:
        if lv.covers_date(d) and lv.is_approved:
            res.is_on_leave = True
            res.leave_request_id = lv.id
            res.leave_is_official_business = lv.is_official_business
            break

    # --- Assignment --------------------------------------------------------
    chosen = _pick_assignment(assignments, d)
    if chosen is not None:
        res.assignment_id = chosen.id
        res.assignment_type = chosen.assignment_type
        if chosen.is_rest_day:
            res.is_rest_day = True
            res.source = "REST_DAY"
            return res
        template = None
        if templates is not None:
            template = templates.get(chosen.shift_template_id)
        if template is None and chosen.shift_template_id:
            template = db.get(ShiftTemplate, chosen.shift_template_id)
        if template is not None:
            _apply_template(res, template, d)
            res.source = "ASSIGNMENT"

    # --- Declared rest day (RestDay table) ---------------------------------
    # Checked after the assignment so that a DAILY_OVERRIDE shift can pull an
    # intern in on their normal rest day, which is routine in 24/7 operations.
    if not res.has_shift:
        if rest_days is None:
            rest_days = list(
                db.scalars(
                    select(RestDay).where(
                        RestDay.intern_id == intern.id,
                        RestDay.is_deleted.is_(False),
                    )
                ).all()
            )
        if any(r.covers_date(d) for r in rest_days):
            res.is_rest_day = True
            res.source = "REST_DAY"

    if res.source == "NONE" and res.is_holiday:
        res.source = "HOLIDAY"
    return res


def resolve_range(
    db: Session,
    intern: Intern,
    start: date,
    end: date,
    *,
    exclude_assignment_id: int | None = None,
) -> dict[date, ResolvedSchedule]:
    """Resolve every date in an inclusive range with a fixed number of queries."""
    assignments = _load_assignments(db, intern.id, start, end, exclude_assignment_id)

    template_ids = {a.shift_template_id for a in assignments if a.shift_template_id}
    templates: dict[int, ShiftTemplate] = {}
    if template_ids:
        for t in db.scalars(
            select(ShiftTemplate).where(ShiftTemplate.id.in_(template_ids))
        ).all():
            templates[t.id] = t

    rest_days = list(
        db.scalars(
            select(RestDay).where(
                RestDay.intern_id == intern.id, RestDay.is_deleted.is_(False)
            )
        ).all()
    )

    hol_stmt = select(Holiday).where(
        Holiday.holiday_date >= start,
        Holiday.holiday_date <= end,
        Holiday.is_deleted.is_(False),
    )
    holidays: dict[date, Holiday] = {}
    for h in db.scalars(hol_stmt).all():
        if h.department_id in (None, intern.department_id):
            holidays[h.holiday_date] = h

    leaves = list(
        db.scalars(
            select(LeaveRequest).where(
                LeaveRequest.intern_id == intern.id,
                LeaveRequest.is_deleted.is_(False),
                LeaveRequest.approval_status == ApprovalStatus.APPROVED,
                LeaveRequest.date_from <= end,
                LeaveRequest.date_to >= start,
            )
        ).all()
    )

    out: dict[date, ResolvedSchedule] = {}
    d = start
    while d <= end:
        out[d] = resolve_for_date(
            db, intern, d,
            templates=templates, assignments=assignments, rest_days=rest_days,
            holidays=holidays, leaves=leaves,
            exclude_assignment_id=exclude_assignment_id,
        )
        d += timedelta(days=1)
    return out


def find_shift_for_timestamp(
    db: Session,
    intern: Intern,
    ts: datetime,
    *,
    early_window_minutes: int = 120,
    late_window_minutes: int = 480,
) -> ResolvedSchedule | None:
    """Which scheduled shift does a punch at ``ts`` belong to?

    This is the heart of correct overnight handling. Both the timestamp's own
    business-local date AND the previous date are considered, because a punch
    at 00:30 on Nov 21 may well belong to the 22:00 shift that started on
    Nov 20.

    A candidate matches when ``ts`` falls within
    ``[start - early_window, start + late_window]``. The nearest start wins, so
    an intern working consecutive evening shifts binds to the right one.

    Returns None when no scheduled shift plausibly claims the punch; the caller
    then records a NO_SCHEDULE session dated by the local calendar date.
    """
    local_date = to_business(ts).date()
    candidates: list[ResolvedSchedule] = []

    for probe in (local_date - timedelta(days=1), local_date, local_date + timedelta(days=1)):
        res = resolve_for_date(db, intern, probe)
        if not res.has_shift:
            continue
        early = res.scheduled_start - timedelta(minutes=early_window_minutes)
        late = res.scheduled_start + timedelta(minutes=late_window_minutes)
        if early <= ts <= late:
            candidates.append(res)

    if not candidates:
        return None

    return min(candidates, key=lambda r: abs((ts - r.scheduled_start).total_seconds()))
