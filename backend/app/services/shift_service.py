"""Standard hourly shift matrix and shift-template management.

The 17 standard templates cover every hourly start from 07:00 to 23:00, each
with a nominal elapsed duration of 9 hours. Templates from SHIFT-09 (15:00)
onward cross midnight and are flagged overnight; the flag is DERIVED from the
times rather than stored, so it can never drift out of step with them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus
from app.core.timeutil import is_overnight, shift_elapsed_minutes, shift_window
from app.models.intern import Intern
from app.models.schedule import ScheduleAssignment, ShiftTemplate

# Default break policy for the standard matrix: 1 hour, unpaid.
# 9h elapsed - 1h unpaid break = 8h expected working hours.
STANDARD_BREAK_MINUTES = 60
STANDARD_BREAK_IS_PAID = False
STANDARD_GRACE_MINUTES = 15
STANDARD_ABSENCE_GRACE_MINUTES = 60
STANDARD_MAX_OVERTIME_MINUTES = 120
STANDARD_MIN_REST_HOURS = 8


def _std(n: int, start_hour: int) -> dict:
    start = time(start_hour, 0)
    end = time((start_hour + 9) % 24, 0)
    return {
        "code": f"SHIFT-{n:02d}",
        "name": f"{start.strftime('%I:%M %p').lstrip('0')} - "
                f"{end.strftime('%I:%M %p').lstrip('0')}",
        "start_time": start,
        "end_time": end,
    }


# SHIFT-01 .. SHIFT-17 : starts 07:00 through 23:00, nine hours each.
STANDARD_SHIFTS: list[dict] = [_std(i, 6 + i) for i in range(1, 18)]


def ensure_standard_shifts(db: Session, actor_user_id: int | None = None) -> int:
    """Create any missing standard templates. Idempotent.

    Existing templates are left untouched: editing a live template would change
    what future assignments mean, and must be a deliberate administrative act
    rather than a side effect of application startup.
    """
    existing = set(db.scalars(select(ShiftTemplate.code)).all())

    created = 0
    for spec in STANDARD_SHIFTS:
        if spec["code"] in existing:
            continue
        db.add(
            ShiftTemplate(
                code=spec["code"],
                name=spec["name"],
                start_time=spec["start_time"],
                end_time=spec["end_time"],
                break_minutes=STANDARD_BREAK_MINUTES,
                break_is_paid=STANDARD_BREAK_IS_PAID,
                grace_period_minutes=STANDARD_GRACE_MINUTES,
                absence_grace_minutes=STANDARD_ABSENCE_GRACE_MINUTES,
                max_overtime_minutes=STANDARD_MAX_OVERTIME_MINUTES,
                min_rest_hours=STANDARD_MIN_REST_HOURS,
                is_active=True,
                is_system_default=True,
                created_by_id=actor_user_id,
            )
        )
        created += 1
    if created:
        db.flush()
    return created


def shift_selector_options(db: Session, include_inactive: bool = False) -> list[dict]:
    """Payload for the visual shift selector.

    Every field the supervisor needs to pick a shift without opening it:
    code, times, overnight indicator, break configuration and net hours.
    """
    stmt = select(ShiftTemplate).where(ShiftTemplate.is_deleted.is_(False))
    if not include_inactive:
        stmt = stmt.where(ShiftTemplate.is_active.is_(True))
    stmt = stmt.order_by(ShiftTemplate.start_time, ShiftTemplate.code)

    options: list[dict] = []
    for t in db.scalars(stmt).all():
        options.append(
            {
                "id": t.id,
                "shift_code": t.code,
                "name": t.name,
                "start_time": t.start_time.strftime("%H:%M"),
                "end_time": t.end_time.strftime("%H:%M"),
                "is_overnight": t.is_overnight,
                "elapsed_minutes": t.elapsed_minutes,
                "elapsed_hours": round(t.elapsed_minutes / 60, 2),
                "break_minutes": t.break_minutes,
                "break_is_paid": t.break_is_paid,
                "unpaid_break_minutes": t.unpaid_break_minutes,
                "net_working_minutes": t.net_working_minutes,
                "net_working_hours": round(t.net_working_minutes / 60, 2),
                "grace_period_minutes": t.grace_period_minutes,
                "max_overtime_minutes": t.max_overtime_minutes,
                "min_rest_hours": t.min_rest_hours,
                "is_active": t.is_active,
                "is_system_default": t.is_system_default,
                "effective_from": t.effective_from.isoformat() if t.effective_from else None,
                "effective_to": t.effective_to.isoformat() if t.effective_to else None,
            }
        )
    return options


# ---------------------------------------------------------------------------
# Conflict validation
# ---------------------------------------------------------------------------

@dataclass
class ConflictFinding:
    code: str
    severity: str  # ERROR | WARNING
    message: str
    detail: dict | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail or {},
        }


@dataclass
class ValidationResult:
    findings: list[ConflictFinding]

    @property
    def errors(self) -> list[ConflictFinding]:
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def warnings(self) -> list[ConflictFinding]:
        return [f for f in self.findings if f.severity == "WARNING"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": [f.as_dict() for f in self.errors],
            "warnings": [f.as_dict() for f in self.warnings],
        }


def validate_assignment(
    db: Session,
    intern: Intern,
    template: ShiftTemplate | None,
    *,
    assignment_type: str,
    day_of_week: int | None,
    specific_date: date | None,
    effective_from: date,
    effective_to: date | None,
    is_rest_day: bool = False,
    exclude_assignment_id: int | None = None,
    min_rest_hours: int | None = None,
    probe_days: int = 28,
) -> ValidationResult:
    """Check an assignment before saving.

    Covers the spec's list: invalid start/end times, inactive templates,
    duplicate assignments, overlapping shifts, conflicting schedules and
    insufficient rest intervals.

    Rest and overlap are checked by materialising the resolved schedule over a
    probe window, because a rotating or temporary pattern can only conflict on
    actual dates - comparing patterns in the abstract gives false positives.
    """
    from app.services.schedule_resolver import resolve_range  # local: cycle

    findings: list[ConflictFinding] = []

    # --- Date sanity --------------------------------------------------------
    if effective_to and effective_to < effective_from:
        findings.append(
            ConflictFinding(
                "INVALID_DATE_RANGE", "ERROR",
                "Effective-to date is before the effective-from date.",
                {"effective_from": str(effective_from), "effective_to": str(effective_to)},
            )
        )

    # --- Template checks ----------------------------------------------------
    if not is_rest_day:
        if template is None:
            findings.append(
                ConflictFinding(
                    "NO_SHIFT_TEMPLATE", "ERROR",
                    "A shift template is required unless the assignment is a rest day.",
                )
            )
        else:
            if template.is_deleted:
                findings.append(
                    ConflictFinding(
                        "TEMPLATE_ARCHIVED", "ERROR",
                        f"Shift template {template.code} is archived.",
                    )
                )
            if not template.is_active:
                findings.append(
                    ConflictFinding(
                        "TEMPLATE_INACTIVE", "ERROR",
                        f"Shift template {template.code} is inactive and cannot be assigned.",
                        {"shift_code": template.code},
                    )
                )
            if template.elapsed_minutes <= 0:
                findings.append(
                    ConflictFinding(
                        "INVALID_SHIFT_TIMES", "ERROR",
                        f"Shift template {template.code} has a zero-length duration.",
                    )
                )
            if template.unpaid_break_minutes >= template.elapsed_minutes:
                findings.append(
                    ConflictFinding(
                        "BREAK_EXCEEDS_SHIFT", "ERROR",
                        f"Unpaid break ({template.unpaid_break_minutes}m) is not shorter "
                        f"than the shift ({template.elapsed_minutes}m); net working time "
                        f"would be zero or negative.",
                    )
                )
            if not template.is_effective_on(effective_from):
                findings.append(
                    ConflictFinding(
                        "TEMPLATE_NOT_EFFECTIVE", "WARNING",
                        f"Shift template {template.code} is not effective on "
                        f"{effective_from}.",
                    )
                )

    # --- Duplicate assignment ----------------------------------------------
    dup_stmt = select(ScheduleAssignment).where(
        ScheduleAssignment.intern_id == intern.id,
        ScheduleAssignment.is_deleted.is_(False),
        ScheduleAssignment.assignment_type == assignment_type,
        ScheduleAssignment.effective_from == effective_from,
    )
    if exclude_assignment_id:
        dup_stmt = dup_stmt.where(ScheduleAssignment.id != exclude_assignment_id)
    for existing in db.scalars(dup_stmt).all():
        same_day = existing.day_of_week == day_of_week
        same_date = existing.specific_date == specific_date
        if not (same_day and same_date):
            continue
        # Same slot, same shift -> a true duplicate.
        # Same slot, DIFFERENT shift -> a conflict, not a duplicate. Reporting
        # it as "identical" would tell the supervisor the wrong thing: the
        # problem is that two different shifts would apply to the same day.
        if existing.shift_template_id == (template.id if template else None) \
                and existing.is_rest_day == is_rest_day:
            findings.append(
                ConflictFinding(
                    "DUPLICATE_ASSIGNMENT", "ERROR",
                    "An identical assignment already exists for this intern "
                    "(same shift, type, weekday and effective date).",
                    {"existing_assignment_id": existing.id},
                )
            )
        else:
            other = existing.shift_template.code if existing.shift_template else "REST DAY"
            wanted = template.code if template else "REST DAY"
            findings.append(
                ConflictFinding(
                    "SCHEDULE_CONFLICT", "ERROR",
                    f"This intern already has {other} assigned to the same day "
                    f"slot at the same precedence; {wanted} would conflict with "
                    f"it. Remove or supersede the existing assignment first.",
                    {
                        "existing_assignment_id": existing.id,
                        "existing_shift": other,
                        "requested_shift": wanted,
                    },
                )
            )

    if findings and any(f.severity == "ERROR" for f in findings):
        # No point probing the calendar if the assignment is already invalid.
        return ValidationResult(findings)

    # --- Overlap and rest, probed over real dates ---------------------------
    probe_start = effective_from
    probe_end = effective_to or (probe_start + timedelta(days=probe_days - 1))
    if (probe_end - probe_start).days > probe_days:
        probe_end = probe_start + timedelta(days=probe_days - 1)

    required_rest = (
        min_rest_hours
        if min_rest_hours is not None
        else (template.min_rest_hours if template else STANDARD_MIN_REST_HOURS)
    )

    # Existing resolved schedule, with the row being edited excluded.
    existing_map = resolve_range(
        db, intern, probe_start, probe_end, exclude_assignment_id=exclude_assignment_id
    )

    # Candidate windows the new assignment would create.
    candidate: dict[date, tuple] = {}
    if template is not None and not is_rest_day:
        from app.models.schedule import ScheduleAssignment as SA

        probe = SA(
            intern_id=intern.id,
            shift_template_id=template.id,
            assignment_type=assignment_type,
            day_of_week=day_of_week,
            specific_date=specific_date,
            effective_from=effective_from,
            effective_to=effective_to,
            is_rest_day=False,
            approval_status=ApprovalStatus.APPROVED,
        )
        d = probe_start
        while d <= probe_end:
            if probe.covers_date(d):
                candidate[d] = shift_window(d, template.start_time, template.end_time)
            d += timedelta(days=1)

    for d, (new_start, new_end) in candidate.items():
        resolved = existing_map.get(d)
        # Same-day collision with a different shift already resolved for that date.
        if resolved and resolved.shift_template_id and not resolved.is_rest_day:
            # A higher-precedence existing row simply wins; equal precedence is
            # a genuine conflict the supervisor must resolve.
            from app.core.enums import ASSIGNMENT_PRECEDENCE

            new_prec = ASSIGNMENT_PRECEDENCE.get(assignment_type, 0)
            old_prec = ASSIGNMENT_PRECEDENCE.get(resolved.assignment_type or "", 0)
            if new_prec == old_prec and resolved.shift_template_id != template.id:
                findings.append(
                    ConflictFinding(
                        "SCHEDULE_CONFLICT", "ERROR",
                        f"{d}: intern already has {resolved.shift_code} assigned at the "
                        f"same precedence; two shifts would apply to the same date.",
                        {"date": str(d), "existing_shift": resolved.shift_code},
                    )
                )

        # Overlap and rest against the neighbouring days' resolved shifts.
        for offset in (-1, 1):
            neighbour_date = d + timedelta(days=offset)
            if neighbour_date in candidate:
                n_start, n_end = candidate[neighbour_date]
            else:
                n = existing_map.get(neighbour_date)
                if not n or not n.scheduled_start or n.is_rest_day:
                    continue
                n_start, n_end = n.scheduled_start, n.scheduled_end

            if n_start < new_end and new_start < n_end:
                findings.append(
                    ConflictFinding(
                        "OVERLAPPING_SHIFTS", "ERROR",
                        f"Shift on {d} overlaps the shift on {neighbour_date}. "
                        f"An overnight shift cannot overlap the next day's shift.",
                        {"date": str(d), "other_date": str(neighbour_date)},
                    )
                )
                continue

            gap_hours = (
                (n_start - new_end).total_seconds() / 3600
                if n_start >= new_end
                else (new_start - n_end).total_seconds() / 3600
            )
            if 0 <= gap_hours < required_rest:
                findings.append(
                    ConflictFinding(
                        "INSUFFICIENT_REST", "WARNING",
                        f"Only {gap_hours:.1f}h rest between the shifts on "
                        f"{min(d, neighbour_date)} and {max(d, neighbour_date)}; "
                        f"{required_rest}h is required.",
                        {
                            "date": str(d),
                            "other_date": str(neighbour_date),
                            "gap_hours": round(gap_hours, 2),
                            "required_rest_hours": required_rest,
                        },
                    )
                )

    # De-duplicate identical findings produced from both days of a pair.
    seen: set[tuple] = set()
    unique: list[ConflictFinding] = []
    for f in findings:
        key = (f.code, f.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    return ValidationResult(unique)
