"""Live IT OJT Duty Board.

An operations-floor view answering, at this instant: who is on duty, who is
due in, who is late with no time-in, who has finished, who is absent, and who
is coming up next.

Overnight shifts are the reason this is not a simple "today's attendance"
query. At 02:00 the interns on duty are working a shift whose business date was
yesterday. The board therefore sweeps a window of business dates around now
(yesterday, today, tomorrow) and classifies by the actual shift window rather
than by the calendar date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import AttendanceStatus, DutyBoardState, InternStatus
from app.core.timeutil import minutes_between, minutes_to_hours, to_business, utc_now
from app.models.attendance import AttendanceRecord
from app.models.intern import Intern
from app.services.schedule_resolver import ResolvedSchedule, resolve_range
from app.services.settings_service import RuleSet, load_rules

# How far ahead a shift counts as "upcoming" rather than merely scheduled.
UPCOMING_HORIZON_HOURS = 12


@dataclass
class DutyEntry:
    intern_id: int
    intern_code: str
    intern_name: str
    team_name: str | None
    supervisor_name: str | None
    technical_function: str | None

    state: str
    business_date: date | None
    shift_code: str | None
    shift_label: str | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    is_overnight: bool

    time_in: datetime | None
    time_out: datetime | None
    on_break_since: datetime | None

    minutes_on_duty: int = 0
    minutes_remaining_in_shift: int = 0
    minutes_until_start: int = 0
    late_minutes: int = 0
    attendance_status: str | None = None
    attendance_record_id: int | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def iso(d):
            return to_business(d).isoformat() if d else None

        return {
            "intern_id": self.intern_id,
            "intern_code": self.intern_code,
            "intern_name": self.intern_name,
            "team": self.team_name,
            "supervisor": self.supervisor_name,
            "technical_function": self.technical_function,
            "state": self.state,
            "business_date": self.business_date.isoformat() if self.business_date else None,
            "shift_code": self.shift_code,
            "shift_label": self.shift_label,
            "scheduled_start": iso(self.scheduled_start),
            "scheduled_end": iso(self.scheduled_end),
            "is_overnight": self.is_overnight,
            "time_in": iso(self.time_in),
            "time_out": iso(self.time_out),
            "on_break_since": iso(self.on_break_since),
            "hours_on_duty": minutes_to_hours(self.minutes_on_duty),
            "minutes_on_duty": self.minutes_on_duty,
            "minutes_remaining_in_shift": self.minutes_remaining_in_shift,
            "minutes_until_start": self.minutes_until_start,
            "late_minutes": self.late_minutes,
            "attendance_status": self.attendance_status,
            "attendance_record_id": self.attendance_record_id,
            "notes": self.notes,
        }


# Board order is also the URGENCY order used to pick one row per intern when
# several shifts fall inside the sweep window. It is ranked by what a
# supervisor must act on now, not chronologically:
#
#   ABSENT outranks EXPECTED_TO_REPORT deliberately. An intern who no-showed
#   the shift that just ended needs a phone call; an intern due in twelve
#   hours needs nothing. Ordering it the other way round hid unexplained
#   absences behind the next day's roster entry.
#
#   COMPLETED_SHIFT stays below EXPECTED_TO_REPORT, because "due in at 07:00"
#   is more useful than "finished yesterday" for the same person.
BOARD_ORDER: list[str] = [
    DutyBoardState.ON_DUTY,
    DutyBoardState.ON_BREAK,
    DutyBoardState.LATE_NO_TIME_IN,
    DutyBoardState.MISSING_TIME_OUT,
    DutyBoardState.ABSENT,
    DutyBoardState.EXPECTED_TO_REPORT,
    DutyBoardState.UPCOMING_SHIFT,
    DutyBoardState.COMPLETED_SHIFT,
    DutyBoardState.ON_LEAVE,
    DutyBoardState.REST_DAY,
]


def _classify(
    intern: Intern,
    resolved: ResolvedSchedule,
    record: AttendanceRecord | None,
    now: datetime,
    rules: RuleSet,
) -> DutyEntry | None:
    """Decide one intern's board state for one candidate shift."""
    entry = DutyEntry(
        intern_id=intern.id,
        intern_code=intern.intern_code,
        intern_name=intern.display_name,
        team_name=intern.team.name if intern.team else None,
        supervisor_name=(
            intern.supervisor.user.full_name
            if intern.supervisor and intern.supervisor.user else None
        ),
        technical_function=intern.primary_technical_function,
        state=DutyBoardState.REST_DAY,
        business_date=resolved.business_date,
        shift_code=resolved.shift_code,
        shift_label=resolved.shift_name,
        scheduled_start=resolved.scheduled_start,
        scheduled_end=resolved.scheduled_end,
        is_overnight=resolved.is_overnight,
        time_in=record.time_in if record else None,
        time_out=record.time_out if record else None,
        on_break_since=None,
        attendance_status=record.status if record else None,
        attendance_record_id=record.id if record else None,
    )

    # --- Open session: on duty right now -----------------------------------
    if record and record.time_in and not record.time_out:
        entry.minutes_on_duty = max(0, minutes_between(record.time_in, now))
        if resolved.scheduled_end:
            entry.minutes_remaining_in_shift = max(
                0, minutes_between(now, resolved.scheduled_end)
            )
        open_break = next(
            (b for b in record.breaks if b.break_end is None and not b.is_deleted), None
        )
        if open_break:
            entry.state = DutyBoardState.ON_BREAK
            entry.on_break_since = open_break.break_start
            entry.notes.append(
                f"On break since {to_business(open_break.break_start).strftime('%H:%M')}"
            )
        elif (
            resolved.scheduled_end
            and now > resolved.scheduled_end + timedelta(
                minutes=resolved.absence_grace_minutes
                or rules.int_("absence_finalize_grace_minutes")
            )
        ):
            # Shift ended well ago and still no time-out.
            entry.state = DutyBoardState.MISSING_TIME_OUT
            entry.notes.append(
                f"Shift ended {to_business(resolved.scheduled_end).strftime('%H:%M')}; "
                f"no time out recorded."
            )
        else:
            entry.state = DutyBoardState.ON_DUTY
        entry.late_minutes = record.late_minutes
        if record.late_minutes:
            entry.notes.append(f"Timed in {record.late_minutes} min late")
        return entry

    # --- Completed session --------------------------------------------------
    if record and record.time_in and record.time_out:
        entry.state = DutyBoardState.COMPLETED_SHIFT
        entry.minutes_on_duty = record.actual_net_minutes
        entry.late_minutes = record.late_minutes
        entry.notes.append(
            f"{minutes_to_hours(record.actual_net_minutes)}h rendered "
            f"({record.status})"
        )
        return entry

    # --- No punches ---------------------------------------------------------
    if resolved.is_on_leave:
        entry.state = DutyBoardState.ON_LEAVE
        return entry
    if resolved.is_rest_day or not resolved.has_shift:
        entry.state = DutyBoardState.REST_DAY
        return entry

    start = resolved.scheduled_start
    end = resolved.scheduled_end
    grace = resolved.grace_period_minutes or rules.int_("default_grace_period_minutes")
    absence_grace = resolved.absence_grace_minutes or rules.int_(
        "absence_finalize_grace_minutes"
    )

    if now < start:
        mins = minutes_between(now, start)
        entry.minutes_until_start = mins
        entry.state = (
            DutyBoardState.EXPECTED_TO_REPORT
            if mins <= UPCOMING_HORIZON_HOURS * 60
            else DutyBoardState.UPCOMING_SHIFT
        )
        entry.notes.append(f"Due in {mins // 60}h {mins % 60}m")
        return entry

    if now > end + timedelta(minutes=absence_grace):
        # Shift finished and grace elapsed with no time-in at all.
        entry.state = DutyBoardState.ABSENT
        entry.notes.append("No time in recorded for a completed shift")
        return entry

    # Shift is in progress (or just ended within grace) and nobody timed in.
    late = max(0, minutes_between(start, now))
    entry.late_minutes = late
    if late <= grace:
        entry.state = DutyBoardState.EXPECTED_TO_REPORT
        entry.notes.append(f"Within the {grace} min grace period")
    else:
        entry.state = DutyBoardState.LATE_NO_TIME_IN
        entry.notes.append(f"{late} min past scheduled start, no time in")
    return entry


def build_duty_board(
    db: Session,
    *,
    now: datetime | None = None,
    rules: RuleSet | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    department_id: int | None = None,
    include_rest_days: bool = False,
) -> dict:
    """Build the live board.

    Returns buckets plus counts. Each intern appears once, under the shift most
    relevant to this instant: an in-progress shift beats an upcoming one.
    """
    rules = rules or load_rules(db)
    now = now or utc_now()
    local_today = to_business(now).date()

    # Sweep yesterday..tomorrow so overnight shifts are caught correctly.
    window_start = local_today - timedelta(days=1)
    window_end = local_today + timedelta(days=1)

    stmt = (
        select(Intern)
        .options(
            selectinload(Intern.team),
            selectinload(Intern.supervisor),
        )
        .where(
            Intern.is_deleted.is_(False),
            Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]),
        )
    )
    if team_id:
        stmt = stmt.where(Intern.team_id == team_id)
    if supervisor_id:
        stmt = stmt.where(Intern.supervisor_id == supervisor_id)
    if department_id:
        stmt = stmt.where(Intern.department_id == department_id)
    interns = list(db.scalars(stmt).all())

    if not interns:
        return {
            "generated_at": to_business(now).isoformat(),
            "buckets": {s: [] for s in BOARD_ORDER},
            "counts": {s: 0 for s in BOARD_ORDER},
            "total_interns": 0,
        }

    # Attendance records in the window, keyed by (intern, business_date).
    records = db.scalars(
        select(AttendanceRecord)
        .options(selectinload(AttendanceRecord.breaks))
        .where(
            AttendanceRecord.intern_id.in_([i.id for i in interns]),
            AttendanceRecord.business_date >= window_start,
            AttendanceRecord.business_date <= window_end,
            AttendanceRecord.is_deleted.is_(False),
        )
    ).all()
    rec_map: dict[tuple[int, date], AttendanceRecord] = {}
    for r in records:
        key = (r.intern_id, r.business_date)
        # Prefer an open session, then the latest sequence.
        prev = rec_map.get(key)
        if prev is None or (r.time_out is None and prev.time_out is not None) or (
            r.session_sequence > prev.session_sequence and prev.time_out is not None
        ):
            rec_map[key] = r

    entries: list[DutyEntry] = []
    for intern in interns:
        schedule = resolve_range(db, intern, window_start, window_end)
        candidates: list[DutyEntry] = []
        for d in sorted(schedule.keys()):
            resolved = schedule[d]
            record = rec_map.get((intern.id, d))
            if not resolved.has_shift and record is None and not resolved.is_on_leave:
                continue
            e = _classify(intern, resolved, record, now, rules)
            if e is not None:
                candidates.append(e)

        if not candidates:
            entries.append(
                DutyEntry(
                    intern_id=intern.id,
                    intern_code=intern.intern_code,
                    intern_name=intern.display_name,
                    team_name=intern.team.name if intern.team else None,
                    supervisor_name=(
                        intern.supervisor.user.full_name
                        if intern.supervisor and intern.supervisor.user else None
                    ),
                    technical_function=intern.primary_technical_function,
                    state=DutyBoardState.REST_DAY,
                    business_date=local_today,
                    shift_code=None, shift_label=None,
                    scheduled_start=None, scheduled_end=None, is_overnight=False,
                    time_in=None, time_out=None, on_break_since=None,
                )
            )
            continue

        # One row per intern: the most operationally urgent state wins.
        priority = {s: idx for idx, s in enumerate(BOARD_ORDER)}
        candidates.sort(key=lambda e: (priority.get(e.state, 99), e.business_date))
        entries.append(candidates[0])

    buckets: dict[str, list[dict]] = {s: [] for s in BOARD_ORDER}
    for e in entries:
        if e.state == DutyBoardState.REST_DAY and not include_rest_days:
            continue
        buckets.setdefault(e.state, []).append(e.as_dict())

    for state, rows in buckets.items():
        if state in (DutyBoardState.EXPECTED_TO_REPORT, DutyBoardState.UPCOMING_SHIFT):
            rows.sort(key=lambda r: r["minutes_until_start"])
        elif state == DutyBoardState.LATE_NO_TIME_IN:
            rows.sort(key=lambda r: -r["late_minutes"])
        else:
            rows.sort(key=lambda r: r["intern_name"])

    return {
        "generated_at": to_business(now).isoformat(),
        "business_timezone": str(to_business(now).tzinfo),
        "window": {"from": str(window_start), "to": str(window_end)},
        "buckets": buckets,
        "counts": {s: len(buckets.get(s, [])) for s in BOARD_ORDER},
        "total_interns": len(interns),
        "on_duty_now": len(buckets.get(DutyBoardState.ON_DUTY, []))
        + len(buckets.get(DutyBoardState.ON_BREAK, [])),
        "needs_attention": (
            len(buckets.get(DutyBoardState.LATE_NO_TIME_IN, []))
            + len(buckets.get(DutyBoardState.MISSING_TIME_OUT, []))
            + len(buckets.get(DutyBoardState.ABSENT, []))
        ),
    }


def shift_handover_view(
    db: Session, *, now: datetime | None = None, rules: RuleSet | None = None
) -> dict:
    """Who is handing over to whom, grouped by shift.

    Supports the handover monitoring requirement: at any moment, the shift
    finishing and the shift starting within the handover window, so the
    supervisor can see who needs to brief whom.
    """
    rules = rules or load_rules(db)
    now = now or utc_now()
    board = build_duty_board(db, now=now, rules=rules, include_rest_days=False)

    HANDOVER_WINDOW_MIN = 120
    outgoing: list[dict] = []
    incoming: list[dict] = []

    for row in board["buckets"].get(DutyBoardState.ON_DUTY, []) + board["buckets"].get(
        DutyBoardState.ON_BREAK, []
    ):
        if 0 <= row["minutes_remaining_in_shift"] <= HANDOVER_WINDOW_MIN:
            outgoing.append(row)
    for state in (DutyBoardState.EXPECTED_TO_REPORT, DutyBoardState.UPCOMING_SHIFT):
        for row in board["buckets"].get(state, []):
            if 0 <= row["minutes_until_start"] <= HANDOVER_WINDOW_MIN:
                incoming.append(row)

    by_shift: dict[str, dict] = {}
    for row in board["buckets"].get(DutyBoardState.ON_DUTY, []):
        code = row["shift_code"] or "UNASSIGNED"
        by_shift.setdefault(code, {"shift_code": code, "on_duty": []})
        by_shift[code]["on_duty"].append(row["intern_name"])

    return {
        "generated_at": to_business(now).isoformat(),
        "handover_window_minutes": HANDOVER_WINDOW_MIN,
        "outgoing": sorted(outgoing, key=lambda r: r["minutes_remaining_in_shift"]),
        "incoming": sorted(incoming, key=lambda r: r["minutes_until_start"]),
        "shifts_on_duty": list(by_shift.values()),
    }
