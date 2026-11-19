"""OJT completion forecast against the school submission deadline.

The question this answers for management is not "when will they finish?" but
"who is going to miss their school's deadline, and by how much?" - early enough
to add shifts and still fix it.

Two independent projections are computed and the later one is reported:

1. PACE  - average approved minutes per week over the recent window, extended
   forward. Reflects what the intern has actually been doing, including
   absences and undertime.
2. SCHEDULE - the intern's actual upcoming assigned shifts, summed day by day
   until the remaining requirement is met. Reflects what they are rostered to
   do.

Reporting the later of the two avoids the optimistic trap of promising a date
the roster cannot deliver, and the pessimistic trap of writing off an intern
whose pace was dragged down by a one-off absence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ForecastRisk, InternStatus
from app.core.timeutil import minutes_to_hours
from app.models.intern import Intern
from app.services.ojt_engine import OJTProgress, average_minutes_per_week, get_progress
from app.services.schedule_resolver import resolve_range
from app.services.settings_service import RuleSet, load_rules

# How far ahead the schedule projection will look before giving up.
MAX_PROJECTION_DAYS = 400


@dataclass
class CompletionForecast:
    intern_id: int
    intern_code: str
    intern_name: str
    team_name: str | None
    supervisor_name: str | None

    required_minutes: int
    approved_minutes: int
    remaining_minutes: int
    completion_percentage: float

    avg_minutes_per_week: int
    scheduled_minutes_remaining: int

    projected_completion_date: date | None
    projection_basis: str  # PACE | SCHEDULE | COMPLETE | NO_DATA
    school_submission_deadline: date | None
    expected_completion_date: date | None

    risk: str
    days_to_deadline: int | None
    days_late: int | None
    hours_short_at_deadline: float | None
    recommended_extra_hours_per_week: float | None
    notes: list[str]

    @property
    def remaining_hours(self) -> float:
        return minutes_to_hours(self.remaining_minutes)

    def as_dict(self) -> dict:
        return {
            "intern_id": self.intern_id,
            "intern_code": self.intern_code,
            "intern_name": self.intern_name,
            "team": self.team_name,
            "supervisor": self.supervisor_name,
            "required_hours": minutes_to_hours(self.required_minutes),
            "approved_hours": minutes_to_hours(self.approved_minutes),
            "remaining_hours": self.remaining_hours,
            "completion_percentage": self.completion_percentage,
            "avg_hours_per_week": minutes_to_hours(self.avg_minutes_per_week),
            "scheduled_hours_remaining": minutes_to_hours(self.scheduled_minutes_remaining),
            "projected_completion_date": (
                self.projected_completion_date.isoformat()
                if self.projected_completion_date else None
            ),
            "projection_basis": self.projection_basis,
            "school_submission_deadline": (
                self.school_submission_deadline.isoformat()
                if self.school_submission_deadline else None
            ),
            "expected_completion_date": (
                self.expected_completion_date.isoformat()
                if self.expected_completion_date else None
            ),
            "risk": self.risk,
            "days_to_deadline": self.days_to_deadline,
            "days_late": self.days_late,
            "hours_short_at_deadline": self.hours_short_at_deadline,
            "recommended_extra_hours_per_week": self.recommended_extra_hours_per_week,
            "notes": self.notes,
        }


def _project_from_pace(
    remaining_minutes: int, avg_per_week: int, today: date
) -> date | None:
    if remaining_minutes <= 0:
        return today
    if avg_per_week <= 0:
        return None
    weeks = remaining_minutes / avg_per_week
    return today + timedelta(days=int(round(weeks * 7)))


def _project_from_schedule(
    db: Session,
    intern: Intern,
    remaining_minutes: int,
    today: date,
    *,
    horizon_days: int = MAX_PROJECTION_DAYS,
) -> tuple[date | None, int]:
    """Walk the actual roster forward until the requirement is met.

    Returns (projected date, total scheduled minutes found in the horizon).
    """
    if remaining_minutes <= 0:
        return today, 0

    end = today + timedelta(days=horizon_days)
    schedule = resolve_range(db, intern, today + timedelta(days=1), end)

    accumulated = 0
    hit: date | None = None
    total_available = 0
    for d in sorted(schedule.keys()):
        res = schedule[d]
        if not res.is_working_day:
            continue
        total_available += res.scheduled_net_minutes
        if hit is None:
            accumulated += res.scheduled_net_minutes
            if accumulated >= remaining_minutes:
                hit = d
    return hit, total_available


def forecast_for_intern(
    db: Session,
    intern: Intern,
    *,
    rules: RuleSet | None = None,
    today: date | None = None,
    progress: OJTProgress | None = None,
) -> CompletionForecast:
    rules = rules or load_rules(db)
    today = today or date.today()
    progress = progress or get_progress(db, intern)
    notes: list[str] = []

    lookback = rules.int_("forecast_lookback_weeks")
    avg_week = average_minutes_per_week(
        db, intern, lookback_weeks=lookback, as_of=today
    )

    deadline = intern.school_submission_deadline
    remaining = progress.remaining_minutes

    # --- Already finished ---------------------------------------------------
    if progress.is_complete:
        return CompletionForecast(
            intern_id=intern.id,
            intern_code=intern.intern_code,
            intern_name=intern.display_name,
            team_name=intern.team.name if intern.team else None,
            supervisor_name=(
                intern.supervisor.user.full_name
                if intern.supervisor and intern.supervisor.user else None
            ),
            required_minutes=progress.required_minutes,
            approved_minutes=progress.approved_minutes,
            remaining_minutes=0,
            completion_percentage=progress.completion_percentage,
            avg_minutes_per_week=avg_week,
            scheduled_minutes_remaining=0,
            projected_completion_date=intern.actual_completion_date or today,
            projection_basis="COMPLETE",
            school_submission_deadline=deadline,
            expected_completion_date=intern.expected_completion_date,
            risk=ForecastRisk.COMPLETED,
            days_to_deadline=(deadline - today).days if deadline else None,
            days_late=None,
            hours_short_at_deadline=None,
            recommended_extra_hours_per_week=None,
            notes=["Required hours already met."],
        )

    # --- Projections --------------------------------------------------------
    pace_date = _project_from_pace(remaining, avg_week, today)
    sched_date, scheduled_available = _project_from_schedule(db, intern, remaining, today)

    if pace_date and sched_date:
        projected = max(pace_date, sched_date)
        basis = "PACE" if projected == pace_date else "SCHEDULE"
        if pace_date != sched_date:
            notes.append(
                f"Pace projects {pace_date}; the roster projects {sched_date}. "
                f"Reporting the later date."
            )
    elif sched_date:
        projected, basis = sched_date, "SCHEDULE"
        if avg_week <= 0:
            notes.append(
                "No approved hours in the recent window, so the projection "
                "relies on the assigned roster only."
            )
    elif pace_date:
        projected, basis = pace_date, "PACE"
        notes.append(
            "No upcoming shifts are assigned, so the projection relies on "
            "recent pace only."
        )
    else:
        projected, basis = None, "NO_DATA"
        notes.append(
            "Cannot project: no approved hours in the recent window and no "
            "upcoming shifts assigned."
        )

    # --- Risk against the school deadline -----------------------------------
    buffer_days = rules.int_("forecast_at_risk_buffer_days")
    days_to_deadline = (deadline - today).days if deadline else None
    days_late: int | None = None
    hours_short: float | None = None
    extra_per_week: float | None = None

    if deadline is None:
        risk = ForecastRisk.NO_DATA if projected is None else ForecastRisk.ON_TRACK
        notes.append(
            "No school submission deadline recorded; set one to enable "
            "deadline risk tracking."
        )
    elif projected is None:
        risk = ForecastRisk.WILL_MISS_DEADLINE
        hours_short = minutes_to_hours(remaining)
        if days_to_deadline and days_to_deadline > 0:
            extra_per_week = round(remaining / 60 / max(1, days_to_deadline / 7), 2)
    elif projected > deadline:
        days_late = (projected - deadline).days
        # What the roster can actually deliver before the deadline.
        _, by_deadline = _project_from_schedule(
            db, intern, remaining, today,
            horizon_days=max(0, (deadline - today).days),
        )
        achievable = min(by_deadline, remaining)
        shortfall_minutes = max(0, remaining - achievable)
        hours_short = minutes_to_hours(shortfall_minutes)

        if shortfall_minutes == 0:
            # The assigned shifts DO cover the requirement before the deadline;
            # it is the recent attendance pace that projects late. Reporting
            # this as WILL_MISS_DEADLINE alongside "short 0 hours" reads as a
            # contradiction and tells the supervisor the wrong thing. The
            # actionable finding is that attendance, not the roster, is the
            # problem.
            risk = ForecastRisk.AT_RISK
            notes.append(
                f"The assigned roster covers the remaining "
                f"{minutes_to_hours(remaining)}h before the {deadline} deadline, "
                f"but the recent pace of "
                f"{minutes_to_hours(avg_week)}h/week projects completion around "
                f"{projected} - about {days_late} day(s) late. Attendance, not "
                f"the schedule, is what needs attention."
            )
        else:
            risk = ForecastRisk.WILL_MISS_DEADLINE
            if days_to_deadline and days_to_deadline > 0:
                weeks_left = max(1.0, days_to_deadline / 7)
                extra_per_week = round(
                    (shortfall_minutes / 60) / weeks_left, 2
                )
            notes.append(
                f"Projected {days_late} day(s) past the {deadline} school "
                f"deadline, and the assigned roster is "
                f"{hours_short}h short of the requirement by then - additional "
                f"shifts are needed, not just better attendance."
            )
    elif (deadline - projected).days <= buffer_days:
        risk = ForecastRisk.AT_RISK
        notes.append(
            f"Projected completion {projected} leaves only "
            f"{(deadline - projected).days} day(s) of margin before the deadline."
        )
    else:
        risk = ForecastRisk.ON_TRACK

    if deadline and days_to_deadline is not None and days_to_deadline < 0:
        notes.append(f"School deadline {deadline} has already passed.")

    return CompletionForecast(
        intern_id=intern.id,
        intern_code=intern.intern_code,
        intern_name=intern.display_name,
        team_name=intern.team.name if intern.team else None,
        supervisor_name=(
            intern.supervisor.user.full_name
            if intern.supervisor and intern.supervisor.user else None
        ),
        required_minutes=progress.required_minutes,
        approved_minutes=progress.approved_minutes,
        remaining_minutes=remaining,
        completion_percentage=progress.completion_percentage,
        avg_minutes_per_week=avg_week,
        scheduled_minutes_remaining=scheduled_available,
        projected_completion_date=projected,
        projection_basis=basis,
        school_submission_deadline=deadline,
        expected_completion_date=intern.expected_completion_date,
        risk=risk,
        days_to_deadline=days_to_deadline,
        days_late=days_late,
        hours_short_at_deadline=hours_short,
        recommended_extra_hours_per_week=extra_per_week,
        notes=notes,
    )


def forecast_all(
    db: Session,
    *,
    rules: RuleSet | None = None,
    today: date | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    risk_filter: list[str] | None = None,
) -> list[CompletionForecast]:
    """Forecast every active intern, worst risk first.

    This is the list a manager actually works from: who needs more shifts.
    """
    rules = rules or load_rules(db)
    stmt = select(Intern).where(
        Intern.is_deleted.is_(False),
        Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]),
    )
    if team_id:
        stmt = stmt.where(Intern.team_id == team_id)
    if supervisor_id:
        stmt = stmt.where(Intern.supervisor_id == supervisor_id)

    order = {
        ForecastRisk.WILL_MISS_DEADLINE: 0,
        ForecastRisk.AT_RISK: 1,
        ForecastRisk.NO_DATA: 2,
        ForecastRisk.ON_TRACK: 3,
        ForecastRisk.COMPLETED: 4,
    }

    out: list[CompletionForecast] = []
    for intern in db.scalars(stmt).all():
        fc = forecast_for_intern(db, intern, rules=rules, today=today)
        if risk_filter and fc.risk not in risk_filter:
            continue
        out.append(fc)

    out.sort(
        key=lambda f: (
            order.get(f.risk, 9),
            -(f.days_late or 0),
            f.days_to_deadline if f.days_to_deadline is not None else 9999,
        )
    )
    return out


def at_risk_summary(
    db: Session, *, rules: RuleSet | None = None, today: date | None = None
) -> dict:
    """Counts by risk level, for the compliance dashboard."""
    forecasts = forecast_all(db, rules=rules, today=today)
    counts: dict[str, int] = {r.value: 0 for r in ForecastRisk}
    for f in forecasts:
        counts[f.risk] = counts.get(f.risk, 0) + 1
    flagged = [
        f for f in forecasts
        if f.risk in (ForecastRisk.WILL_MISS_DEADLINE, ForecastRisk.AT_RISK)
    ]
    return {
        "counts": counts,
        "total_interns": len(forecasts),
        "flagged_count": len(flagged),
        "flagged": [f.as_dict() for f in flagged],
    }
