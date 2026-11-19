"""Management dashboard and compliance monitoring.

Two related views:

``management_dashboard``  the KPI set and analytics the spec lists, for a date
                          range, computed from real records.
``compliance_dashboard``  the cross-team compliance picture: attendance
                          compliance, OJT completion, absences, and the
                          supervisor approval backlog with aging.

Everything here derives from attendance records and the hour ledger. No
figure is read from a cached total.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import (
    ApprovalStatus, AttendanceStatus, ForecastRisk, InternStatus,
)
from app.core.timeutil import (
    date_range, minutes_to_hours, to_business, utc_now, week_start,
)
from app.models.attendance import AttendanceAdjustment, AttendanceRecord
from app.models.intern import Intern
from app.models.leave import LeaveRequest
from app.models.ojt import OJTHourLedger
from app.models.org import Supervisor, Team
from app.models.timesheet import Timesheet
from app.services.forecast_service import forecast_all
from app.services.ojt_engine import get_progress
from app.services.settings_service import RuleSet, load_rules

# Statuses that mean the intern reported for duty.
PRESENT_STATUSES = (
    AttendanceStatus.PRESENT,
    AttendanceStatus.LATE,
    AttendanceStatus.UNDERTIME,
    AttendanceStatus.HALF_DAY,
)
# Statuses that excuse a non-appearance; excluded from compliance denominators.
EXCUSED_STATUSES = (
    AttendanceStatus.ON_LEAVE,
    AttendanceStatus.OFFICIAL_BUSINESS,
    AttendanceStatus.REST_DAY,
    AttendanceStatus.HOLIDAY,
    AttendanceStatus.NO_SCHEDULE,
)


def resolve_date_range(preset: str | None, today: date | None = None,
                       date_from: date | None = None,
                       date_to: date | None = None) -> tuple[date, date, str]:
    """Translate a dashboard filter preset into concrete bounds."""
    today = today or date.today()
    preset = (preset or "TODAY").upper()

    if preset == "CUSTOM":
        if not (date_from and date_to):
            raise ValueError("CUSTOM range requires date_from and date_to.")
        return date_from, date_to, "CUSTOM"
    if preset == "TODAY":
        return today, today, "TODAY"
    if preset == "THIS_WEEK":
        return week_start(today), week_start(today) + timedelta(days=6), "THIS_WEEK"
    if preset == "THIS_MONTH":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1), "THIS_MONTH"
    if preset == "THIS_QUARTER":
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        end_month = q * 3 + 3
        nxt = (date(today.year, end_month, 1) + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1), "THIS_QUARTER"
    return today, today, "TODAY"


def _intern_scope(
    team_id: int | None, supervisor_id: int | None, department_id: int | None
) -> list:
    conds = [
        Intern.is_deleted.is_(False),
    ]
    if team_id:
        conds.append(Intern.team_id == team_id)
    if supervisor_id:
        conds.append(Intern.supervisor_id == supervisor_id)
    if department_id:
        conds.append(Intern.department_id == department_id)
    return conds


def management_dashboard(
    db: Session,
    *,
    preset: str = "TODAY",
    date_from: date | None = None,
    date_to: date | None = None,
    today: date | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    department_id: int | None = None,
    rules: RuleSet | None = None,
) -> dict:
    """The KPI cards and analytics series."""
    rules = rules or load_rules(db)
    today = today or date.today()
    start, end, label = resolve_date_range(preset, today, date_from, date_to)
    scope = _intern_scope(team_id, supervisor_id, department_id)

    interns = list(db.scalars(select(Intern).where(and_(*scope))).all())
    active = [i for i in interns if i.status in (InternStatus.ACTIVE, InternStatus.ON_LEAVE)]
    completed = [i for i in interns if i.status == InternStatus.COMPLETED]
    intern_ids = [i.id for i in interns]

    # --- Today's counts -----------------------------------------------------
    def status_counts(d: date) -> dict[str, int]:
        if not intern_ids:
            return {}
        rows = db.execute(
            select(AttendanceRecord.status, func.count(AttendanceRecord.id))
            .where(
                AttendanceRecord.intern_id.in_(intern_ids),
                AttendanceRecord.business_date == d,
                AttendanceRecord.is_deleted.is_(False),
            )
            .group_by(AttendanceRecord.status)
        ).all()
        return {s: c for s, c in rows}

    todays = status_counts(today)
    present_today = sum(todays.get(s, 0) for s in PRESENT_STATUSES)
    absent_today = todays.get(AttendanceStatus.ABSENT, 0)
    late_today = todays.get(AttendanceStatus.LATE, 0)
    on_duty = todays.get(AttendanceStatus.IN_PROGRESS, 0)

    # On duty must also catch overnight sessions whose business date is
    # yesterday but which are still open right now.
    open_sessions = db.scalar(
        select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.intern_id.in_(intern_ids) if intern_ids else False,
            AttendanceRecord.is_deleted.is_(False),
            AttendanceRecord.time_in.is_not(None),
            AttendanceRecord.time_out.is_(None),
        )
    ) or 0

    # --- Hours --------------------------------------------------------------
    live = [
        OJTHourLedger.is_reversed.is_(False),
        OJTHourLedger.reversal_of_id.is_(None),
    ]
    total_rendered = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(OJTHourLedger.intern_id.in_(intern_ids) if intern_ids else False,
                 OJTHourLedger.is_approved.is_(True), *live)
        )
    ) or 0
    pending_hours = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(OJTHourLedger.intern_id.in_(intern_ids) if intern_ids else False,
                 OJTHourLedger.is_approved.is_(False), *live)
        )
    ) or 0
    period_rendered = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(OJTHourLedger.intern_id.in_(intern_ids) if intern_ids else False,
                 OJTHourLedger.is_approved.is_(True),
                 OJTHourLedger.business_date >= start,
                 OJTHourLedger.business_date <= end, *live)
        )
    ) or 0

    required_total = sum(i.required_minutes for i in active)
    # Per-intern capping, so one over-rendering intern cannot mask another's
    # shortfall in the aggregate completion figure.
    credited_total = 0
    remaining_total = 0
    for i in active:
        p = get_progress(db, i)
        credited_total += p.credited_minutes
        remaining_total += p.remaining_minutes

    overall_completion = (
        round(credited_total / required_total * 100, 2) if required_total else 0.0
    )

    # --- Attendance compliance over the range -------------------------------
    compliance = attendance_compliance(
        db, start, end, intern_ids=intern_ids, rules=rules
    )

    # --- Analytics series ---------------------------------------------------
    daily_rows = db.execute(
        select(
            AttendanceRecord.business_date,
            AttendanceRecord.status,
            func.count(AttendanceRecord.id),
        )
        .where(
            AttendanceRecord.intern_id.in_(intern_ids) if intern_ids else False,
            AttendanceRecord.business_date >= start,
            AttendanceRecord.business_date <= end,
            AttendanceRecord.is_deleted.is_(False),
        )
        .group_by(AttendanceRecord.business_date, AttendanceRecord.status)
        .order_by(AttendanceRecord.business_date)
    ).all()
    daily: dict[str, dict] = {}
    for d, status, count in daily_rows:
        key = d.isoformat()
        daily.setdefault(key, {"date": key, "present": 0, "absent": 0, "late": 0, "other": 0})
        if status in PRESENT_STATUSES:
            daily[key]["present"] += count
            if status == AttendanceStatus.LATE:
                daily[key]["late"] += count
        elif status == AttendanceStatus.ABSENT:
            daily[key]["absent"] += count
        else:
            daily[key]["other"] += count

    hours_rows = db.execute(
        select(
            OJTHourLedger.business_date,
            func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0),
        )
        .where(
            and_(OJTHourLedger.intern_id.in_(intern_ids) if intern_ids else False,
                 OJTHourLedger.is_approved.is_(True),
                 OJTHourLedger.business_date >= start,
                 OJTHourLedger.business_date <= end, *live)
        )
        .group_by(OJTHourLedger.business_date)
        .order_by(OJTHourLedger.business_date)
    ).all()

    # --- By team ------------------------------------------------------------
    by_team = attendance_by_team(db, start, end, rules=rules, department_id=department_id)

    # --- Individual progress ------------------------------------------------
    progress_rows = []
    for i in active:
        p = get_progress(db, i)
        progress_rows.append(
            {
                "intern_id": i.id,
                "intern_code": i.intern_code,
                "intern_name": i.display_name,
                "team": i.team.name if i.team else None,
                "required_hours": minutes_to_hours(p.required_minutes),
                "approved_hours": minutes_to_hours(p.approved_minutes),
                "remaining_hours": minutes_to_hours(p.remaining_minutes),
                "completion_percentage": p.completion_percentage,
                "milestone": p.milestone,
            }
        )
    progress_rows.sort(key=lambda r: -r["completion_percentage"])

    return {
        "generated_at": to_business(utc_now()).isoformat(),
        "filter": {
            "preset": label,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "team_id": team_id,
            "supervisor_id": supervisor_id,
            "department_id": department_id,
        },
        "kpi": {
            "total_active_interns": len(active),
            "total_present_today": present_today,
            "total_absent_today": absent_today,
            "total_late_today": late_today,
            "total_on_duty": int(open_sessions),
            "total_completed_ojt": len(completed),
            "total_hours_rendered": minutes_to_hours(int(total_rendered)),
            "total_remaining_hours": minutes_to_hours(remaining_total),
            "overall_completion_percentage": overall_completion,
            "attendance_compliance_percentage": compliance["compliance_percentage"],
            "pending_approval_hours": minutes_to_hours(int(pending_hours)),
            "hours_rendered_in_period": minutes_to_hours(int(period_rendered)),
        },
        "analytics": {
            "daily_attendance": list(daily.values()),
            "hours_per_day": [
                {"date": d.isoformat(), "hours": minutes_to_hours(int(m))}
                for d, m in hours_rows
            ],
            "hours_rendered_vs_required": {
                "required_hours": minutes_to_hours(required_total),
                "rendered_hours": minutes_to_hours(credited_total),
                "remaining_hours": minutes_to_hours(remaining_total),
            },
            "attendance_by_team": by_team,
            "individual_progress": progress_rows,
            "status_distribution_today": [
                {"status": k, "count": v} for k, v in sorted(todays.items())
            ],
        },
    }


def attendance_compliance(
    db: Session,
    start: date,
    end: date,
    *,
    intern_ids: list[int] | None = None,
    rules: RuleSet | None = None,
) -> dict:
    """Attendance compliance over a range.

    compliance % = reported days / days the intern was actually expected

    Rest days, holidays, approved leave and days with no schedule are excluded
    from the denominator. Counting them would let an intern on three weeks of
    approved leave appear non-compliant, or an intern rostered one day a week
    look identical to one rostered five.
    """
    rules = rules or load_rules(db)
    conds = [
        AttendanceRecord.business_date >= start,
        AttendanceRecord.business_date <= end,
        AttendanceRecord.is_deleted.is_(False),
    ]
    if intern_ids is not None:
        if not intern_ids:
            return {
                "expected_days": 0, "reported_days": 0, "absent_days": 0,
                "late_days": 0, "excused_days": 0, "compliance_percentage": 0.0,
                "punctuality_percentage": 0.0, "target": rules.float_("attendance_compliance_target"),
                "meets_target": False,
            }
        conds.append(AttendanceRecord.intern_id.in_(intern_ids))

    rows = db.execute(
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .where(and_(*conds))
        .group_by(AttendanceRecord.status)
    ).all()
    counts = {s: c for s, c in rows}

    reported = sum(counts.get(s, 0) for s in PRESENT_STATUSES)
    absent = counts.get(AttendanceStatus.ABSENT, 0)
    incomplete = counts.get(AttendanceStatus.INCOMPLETE_TIME_RECORD, 0)
    late = counts.get(AttendanceStatus.LATE, 0)
    excused = sum(counts.get(s, 0) for s in EXCUSED_STATUSES)

    expected = reported + absent + incomplete
    compliance_pct = round(reported / expected * 100, 2) if expected else 0.0
    punctual = reported - late
    punctuality_pct = round(punctual / reported * 100, 2) if reported else 0.0
    target = rules.float_("attendance_compliance_target")

    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "expected_days": expected,
        "reported_days": reported,
        "absent_days": absent,
        "incomplete_days": incomplete,
        "late_days": late,
        "excused_days": excused,
        "compliance_percentage": compliance_pct,
        "punctuality_percentage": punctuality_pct,
        "target": target,
        "meets_target": compliance_pct >= target,
    }


def attendance_by_team(
    db: Session,
    start: date,
    end: date,
    *,
    rules: RuleSet | None = None,
    department_id: int | None = None,
) -> list[dict]:
    """Compliance and completion per IT team."""
    rules = rules or load_rules(db)
    stmt = select(Team).where(Team.is_deleted.is_(False))
    if department_id:
        stmt = stmt.where(Team.department_id == department_id)
    teams = list(db.scalars(stmt).all())

    out: list[dict] = []
    for team in teams:
        members = list(
            db.scalars(
                select(Intern).where(
                    Intern.team_id == team.id,
                    Intern.is_deleted.is_(False),
                    Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]),
                )
            ).all()
        )
        ids = [i.id for i in members]
        comp = attendance_compliance(db, start, end, intern_ids=ids, rules=rules)
        req = sum(i.required_minutes for i in members)
        cred = sum(get_progress(db, i).credited_minutes for i in members)
        out.append(
            {
                "team_id": team.id,
                "team_code": team.code,
                "team_name": team.name,
                "technical_function": team.technical_function,
                "intern_count": len(members),
                "compliance_percentage": comp["compliance_percentage"],
                "punctuality_percentage": comp["punctuality_percentage"],
                "absent_days": comp["absent_days"],
                "late_days": comp["late_days"],
                "expected_days": comp["expected_days"],
                "completion_percentage": (
                    round(cred / req * 100, 2) if req else 0.0
                ),
                "meets_target": comp["meets_target"],
            }
        )
    out.sort(key=lambda r: r["compliance_percentage"])
    return out


def approval_backlog(
    db: Session,
    *,
    rules: RuleSet | None = None,
    now: datetime | None = None,
    supervisor_id: int | None = None,
) -> dict:
    """Pending approvals per supervisor, with aging.

    Backlog is what actually stalls an intern's OJT credit: unapproved
    attendance does not count toward approved hours, so a supervisor who is
    behind is silently holding back their interns' completion.
    """
    rules = rules or load_rules(db)
    now = now or utc_now()
    warn_days = rules.int_("approval_backlog_warning_days")
    cutoff = now - timedelta(days=warn_days)

    supervisors = list(
        db.scalars(
            select(Supervisor)
            .options(selectinload(Supervisor.user), selectinload(Supervisor.interns))
            .where(Supervisor.is_deleted.is_(False))
        ).all()
    )
    if supervisor_id:
        supervisors = [s for s in supervisors if s.id == supervisor_id]

    rows: list[dict] = []
    totals = {"attendance": 0, "corrections": 0, "leave": 0, "timesheets": 0, "overdue": 0}

    for sup in supervisors:
        ids = [i.id for i in sup.interns if not i.is_deleted]
        if not ids:
            rows.append(
                {
                    "supervisor_id": sup.id,
                    "supervisor_name": sup.user.full_name if sup.user else "(no user)",
                    "intern_count": 0,
                    "pending_attendance": 0, "pending_corrections": 0,
                    "pending_leave": 0, "pending_timesheets": 0,
                    "overdue_items": 0, "oldest_pending_days": None,
                    "total_pending": 0, "status": "CLEAR",
                }
            )
            continue

        pend_att = db.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.intern_id.in_(ids),
                AttendanceRecord.is_deleted.is_(False),
                AttendanceRecord.time_in.is_not(None),
                AttendanceRecord.approval_status == ApprovalStatus.PENDING,
            )
        ) or 0
        pend_corr = db.scalar(
            select(func.count(AttendanceAdjustment.id)).where(
                AttendanceAdjustment.intern_id.in_(ids),
                AttendanceAdjustment.is_deleted.is_(False),
                AttendanceAdjustment.approval_status == ApprovalStatus.PENDING,
            )
        ) or 0
        pend_leave = db.scalar(
            select(func.count(LeaveRequest.id)).where(
                LeaveRequest.intern_id.in_(ids),
                LeaveRequest.is_deleted.is_(False),
                LeaveRequest.approval_status == ApprovalStatus.PENDING,
            )
        ) or 0
        pend_ts = db.scalar(
            select(func.count(Timesheet.id)).where(
                Timesheet.intern_id.in_(ids),
                Timesheet.is_deleted.is_(False),
                Timesheet.status == "SUBMITTED",
            )
        ) or 0

        overdue = db.scalar(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.intern_id.in_(ids),
                AttendanceRecord.is_deleted.is_(False),
                AttendanceRecord.time_in.is_not(None),
                AttendanceRecord.approval_status == ApprovalStatus.PENDING,
                AttendanceRecord.created_at < cutoff,
            )
        ) or 0

        oldest = db.scalar(
            select(func.min(AttendanceRecord.created_at)).where(
                AttendanceRecord.intern_id.in_(ids),
                AttendanceRecord.is_deleted.is_(False),
                AttendanceRecord.time_in.is_not(None),
                AttendanceRecord.approval_status == ApprovalStatus.PENDING,
            )
        )
        oldest_days = (now - oldest).days if oldest else None

        total = pend_att + pend_corr + pend_leave + pend_ts
        status = (
            "CRITICAL" if overdue > 10 or (oldest_days or 0) > warn_days * 3
            else "BEHIND" if overdue > 0
            else "PENDING" if total > 0
            else "CLEAR"
        )

        rows.append(
            {
                "supervisor_id": sup.id,
                "supervisor_name": sup.user.full_name if sup.user else "(no user)",
                "intern_count": len(ids),
                "pending_attendance": int(pend_att),
                "pending_corrections": int(pend_corr),
                "pending_leave": int(pend_leave),
                "pending_timesheets": int(pend_ts),
                "overdue_items": int(overdue),
                "oldest_pending_days": oldest_days,
                "total_pending": int(total),
                "status": status,
            }
        )
        totals["attendance"] += int(pend_att)
        totals["corrections"] += int(pend_corr)
        totals["leave"] += int(pend_leave)
        totals["timesheets"] += int(pend_ts)
        totals["overdue"] += int(overdue)

    rows.sort(key=lambda r: (-r["overdue_items"], -r["total_pending"]))
    return {
        "warning_threshold_days": warn_days,
        "totals": totals,
        "total_pending": sum(
            [totals["attendance"], totals["corrections"], totals["leave"],
             totals["timesheets"]]
        ),
        "supervisors": rows,
        "supervisors_behind": [r for r in rows if r["status"] in ("BEHIND", "CRITICAL")],
    }


def excessive_absences(
    db: Session, *, rules: RuleSet | None = None, today: date | None = None
) -> list[dict]:
    """Interns over the absence threshold in the rolling review window."""
    rules = rules or load_rules(db)
    today = today or date.today()
    window = rules.int_("absence_review_window_days")
    threshold = rules.int_("excessive_absence_threshold")
    start = today - timedelta(days=window)

    rows = db.execute(
        select(
            AttendanceRecord.intern_id,
            func.count(AttendanceRecord.id).label("absences"),
        )
        .where(
            AttendanceRecord.status == AttendanceStatus.ABSENT,
            AttendanceRecord.business_date >= start,
            AttendanceRecord.business_date <= today,
            AttendanceRecord.is_deleted.is_(False),
        )
        .group_by(AttendanceRecord.intern_id)
        .having(func.count(AttendanceRecord.id) >= threshold)
        .order_by(func.count(AttendanceRecord.id).desc())
    ).all()

    out: list[dict] = []
    for intern_id, absences in rows:
        intern = db.get(Intern, intern_id)
        if intern is None or intern.is_deleted:
            continue
        out.append(
            {
                "intern_id": intern.id,
                "intern_code": intern.intern_code,
                "intern_name": intern.display_name,
                "team": intern.team.name if intern.team else None,
                "supervisor": (
                    intern.supervisor.user.full_name
                    if intern.supervisor and intern.supervisor.user else None
                ),
                "absences": int(absences),
                "threshold": threshold,
                "window_days": window,
            }
        )
    return out


def compliance_dashboard(
    db: Session,
    *,
    preset: str = "THIS_MONTH",
    date_from: date | None = None,
    date_to: date | None = None,
    today: date | None = None,
    department_id: int | None = None,
    rules: RuleSet | None = None,
) -> dict:
    """Management Compliance Dashboard across all IT teams."""
    rules = rules or load_rules(db)
    today = today or date.today()
    start, end, label = resolve_date_range(preset, today, date_from, date_to)

    scope = _intern_scope(None, None, department_id)
    interns = list(
        db.scalars(
            select(Intern).where(
                and_(*scope,
                     Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]))
            )
        ).all()
    )
    ids = [i.id for i in interns]

    overall = attendance_compliance(db, start, end, intern_ids=ids, rules=rules)
    teams = attendance_by_team(db, start, end, rules=rules, department_id=department_id)
    backlog = approval_backlog(db, rules=rules)
    absences = excessive_absences(db, rules=rules, today=today)
    forecasts = forecast_all(db, rules=rules, today=today)

    req = sum(i.required_minutes for i in interns)
    cred = sum(get_progress(db, i).credited_minutes for i in interns)

    at_risk = [
        f for f in forecasts
        if f.risk in (ForecastRisk.WILL_MISS_DEADLINE, ForecastRisk.AT_RISK)
    ]
    near_complete = [
        f for f in forecasts
        if f.completion_percentage >= rules.float_("completion_warning_percentage")
        and f.risk != ForecastRisk.COMPLETED
    ]

    alerts: list[dict] = []
    if not overall["meets_target"]:
        alerts.append({
            "severity": "WARNING",
            "message": (
                f"Attendance compliance {overall['compliance_percentage']}% is below "
                f"the {overall['target']}% target."
            ),
        })
    if backlog["totals"]["overdue"]:
        alerts.append({
            "severity": "WARNING",
            "message": (
                f"{backlog['totals']['overdue']} approval(s) older than "
                f"{backlog['warning_threshold_days']} days are holding back OJT credit."
            ),
        })
    for f in at_risk:
        if f.risk == ForecastRisk.WILL_MISS_DEADLINE:
            alerts.append({
                "severity": "CRITICAL",
                "message": (
                    f"{f.intern_name} ({f.intern_code}) will miss the "
                    f"{f.school_submission_deadline} school deadline by "
                    f"{f.days_late} day(s), short {f.hours_short_at_deadline}h."
                ),
            })
    for a in absences:
        alerts.append({
            "severity": "WARNING",
            "message": (
                f"{a['intern_name']} has {a['absences']} absences in the last "
                f"{a['window_days']} days."
            ),
        })

    return {
        "generated_at": to_business(utc_now()).isoformat(),
        "filter": {
            "preset": label,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "department_id": department_id,
        },
        "summary": {
            "active_interns": len(interns),
            "attendance_compliance_percentage": overall["compliance_percentage"],
            "attendance_target": overall["target"],
            "meets_attendance_target": overall["meets_target"],
            "punctuality_percentage": overall["punctuality_percentage"],
            "ojt_completion_percentage": round(cred / req * 100, 2) if req else 0.0,
            "total_absent_days": overall["absent_days"],
            "total_late_days": overall["late_days"],
            "total_pending_approvals": backlog["total_pending"],
            "overdue_approvals": backlog["totals"]["overdue"],
            "supervisors_behind": len(backlog["supervisors_behind"]),
            "interns_at_risk": len(at_risk),
            "interns_near_completion": len(near_complete),
            "interns_with_excessive_absences": len(absences),
        },
        "attendance": overall,
        "by_team": teams,
        "approval_backlog": backlog,
        "excessive_absences": absences,
        "at_risk_interns": [f.as_dict() for f in at_risk],
        "near_completion": [f.as_dict() for f in near_complete],
        "alerts": sorted(
            alerts, key=lambda a: 0 if a["severity"] == "CRITICAL" else 1
        ),
    }
