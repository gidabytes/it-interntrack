"""Report generation: data assembly plus Excel, CSV and PDF rendering.

Each report returns a plain structure (title, columns, rows, meta) so one
renderer can serve every format. Adding a report means adding a builder, not a
new export path.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus, AttendanceStatus, InternStatus
from app.core.timeutil import fmt_hhmm, minutes_to_hours, to_business, utc_now
from app.models.attendance import AttendanceAdjustment, AttendanceRecord
from app.models.intern import Intern
from app.models.leave import LeaveRequest
from app.models.ojt import OJTHourLedger
from app.services import dashboard_service, exposure_service, forecast_service
from app.services.ojt_engine import get_progress
from app.services.schedule_resolver import resolve_range
from app.services.settings_service import RuleSet, load_rules


@dataclass
class Report:
    key: str
    title: str
    columns: list[str]
    rows: list[list]
    meta: dict = field(default_factory=dict)
    # Column keys whose values are numeric, used for totals in Excel.
    numeric_columns: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "meta": self.meta,
        }


def _scoped_interns(
    db: Session,
    *,
    intern_id: int | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    include_inactive: bool = False,
) -> list[Intern]:
    conds = [Intern.is_deleted.is_(False)]
    if not include_inactive:
        conds.append(
            Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE,
                               InternStatus.COMPLETED])
        )
    if intern_id:
        conds.append(Intern.id == intern_id)
    if team_id:
        conds.append(Intern.team_id == team_id)
    if supervisor_id:
        conds.append(Intern.supervisor_id == supervisor_id)
    return list(
        db.scalars(
            select(Intern).where(and_(*conds)).order_by(
                Intern.last_name, Intern.first_name
            )
        ).all()
    )


def _attendance_rows(
    db: Session, interns: list[Intern], start: date, end: date,
    statuses: tuple[str, ...] | None = None,
) -> list[AttendanceRecord]:
    if not interns:
        return []
    conds = [
        AttendanceRecord.intern_id.in_([i.id for i in interns]),
        AttendanceRecord.business_date >= start,
        AttendanceRecord.business_date <= end,
        AttendanceRecord.is_deleted.is_(False),
    ]
    if statuses:
        conds.append(AttendanceRecord.status.in_(statuses))
    return list(
        db.scalars(
            select(AttendanceRecord).where(and_(*conds)).order_by(
                AttendanceRecord.business_date, AttendanceRecord.intern_id
            )
        ).all()
    )


def _hhmm_time(dt: datetime | None) -> str:
    return to_business(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def daily_attendance(db, start, end, **kw) -> Report:
    interns = _scoped_interns(db, **kw)
    by_id = {i.id: i for i in interns}
    rows = []
    for r in _attendance_rows(db, interns, start, end):
        i = by_id[r.intern_id]
        rows.append([
            r.business_date.isoformat(), i.intern_code, i.display_name,
            i.team.name if i.team else "", r.shift_code or "",
            "Yes" if r.is_overnight_shift else "No",
            _hhmm_time(r.time_in), _hhmm_time(r.time_out),
            fmt_hhmm(r.scheduled_net_minutes), fmt_hhmm(r.actual_net_minutes),
            fmt_hhmm(r.creditable_minutes), r.late_minutes, r.undertime_minutes,
            r.approved_overtime_minutes, r.status, r.approval_status,
            r.supervisor_remarks or "",
        ])
    return Report(
        key="daily_attendance",
        title=f"Daily Attendance Report ({start} to {end})",
        columns=[
            "Business Date", "Intern Code", "Name", "Team", "Shift", "Overnight",
            "Time In", "Time Out", "Scheduled", "Actual Rendered", "Creditable",
            "Late (min)", "Undertime (min)", "Overtime (min)", "Status",
            "Approval", "Remarks",
        ],
        rows=rows,
        numeric_columns=[11, 12, 13],
        meta={"date_from": str(start), "date_to": str(end),
              "intern_count": len(interns)},
    )


def _period_summary(db, start, end, group: str, **kw) -> Report:
    interns = _scoped_interns(db, **kw)
    by_id = {i.id: i for i in interns}
    records = _attendance_rows(db, interns, start, end)

    agg: dict[tuple, dict] = {}
    for r in records:
        i = by_id[r.intern_id]
        if group == "WEEK":
            from app.core.timeutil import week_start
            key_label = f"Week of {week_start(r.business_date)}"
        else:
            key_label = r.business_date.strftime("%Y-%m")
        key = (r.intern_id, key_label)
        a = agg.setdefault(key, {
            "intern": i, "label": key_label, "present": 0, "absent": 0, "late": 0,
            "undertime": 0, "scheduled": 0, "actual": 0, "creditable": 0,
            "late_min": 0, "ot": 0,
        })
        if r.status in dashboard_service.PRESENT_STATUSES:
            a["present"] += 1
        if r.status == AttendanceStatus.ABSENT:
            a["absent"] += 1
        if r.is_late:
            a["late"] += 1
        if r.undertime_minutes:
            a["undertime"] += 1
        a["scheduled"] += r.scheduled_net_minutes
        a["actual"] += r.actual_net_minutes
        a["creditable"] += r.creditable_minutes
        a["late_min"] += r.late_minutes
        a["ot"] += r.approved_overtime_minutes

    rows = []
    for (_, _), a in sorted(
        agg.items(), key=lambda kv: (kv[1]["label"], kv[1]["intern"].display_name)
    ):
        i = a["intern"]
        rows.append([
            a["label"], i.intern_code, i.display_name,
            i.team.name if i.team else "",
            a["present"], a["absent"], a["late"], a["undertime"],
            minutes_to_hours(a["scheduled"]), minutes_to_hours(a["actual"]),
            minutes_to_hours(a["creditable"]), a["late_min"],
            minutes_to_hours(a["ot"]),
        ])
    name = "Weekly" if group == "WEEK" else "Monthly"
    return Report(
        key=f"{name.lower()}_attendance_summary",
        title=f"{name} Attendance Summary ({start} to {end})",
        columns=[
            "Period", "Intern Code", "Name", "Team", "Days Present", "Days Absent",
            "Days Late", "Undertime Days", "Scheduled Hours", "Actual Hours",
            "Creditable Hours", "Late (min)", "Overtime Hours",
        ],
        rows=rows,
        numeric_columns=[4, 5, 6, 7, 8, 9, 10, 11, 12],
        meta={"date_from": str(start), "date_to": str(end)},
    )


def weekly_summary(db, start, end, **kw) -> Report:
    return _period_summary(db, start, end, "WEEK", **kw)


def monthly_summary(db, start, end, **kw) -> Report:
    return _period_summary(db, start, end, "MONTH", **kw)


def individual_timesheet(db, start, end, *, intern_id: int, **kw) -> Report:
    """Day-by-day timesheet for one intern, including non-working days."""
    intern = db.get(Intern, intern_id)
    if intern is None:
        raise ValueError("Intern not found.")
    schedule = resolve_range(db, intern, start, end)
    records = {}
    for r in _attendance_rows(db, [intern], start, end):
        records.setdefault(r.business_date, []).append(r)

    rows = []
    for d in sorted(schedule.keys()):
        res = schedule[d]
        recs = records.get(d, [])
        if not recs:
            rows.append([
                d.isoformat(), d.strftime("%a"), res.shift_code or "",
                "", "", fmt_hhmm(res.scheduled_net_minutes), "0:00", "0:00",
                0, 0, 0,
                AttendanceStatus.REST_DAY if res.is_rest_day
                else AttendanceStatus.HOLIDAY if res.is_holiday
                else AttendanceStatus.ON_LEAVE if res.is_on_leave
                else AttendanceStatus.NO_SCHEDULE,
                "",
            ])
            continue
        for r in recs:
            rows.append([
                d.isoformat(), d.strftime("%a"), r.shift_code or "",
                _hhmm_time(r.time_in), _hhmm_time(r.time_out),
                fmt_hhmm(r.scheduled_net_minutes), fmt_hhmm(r.actual_net_minutes),
                fmt_hhmm(r.creditable_minutes),
                r.late_minutes, r.undertime_minutes, r.approved_overtime_minutes,
                r.status, r.supervisor_remarks or "",
            ])

    progress = get_progress(db, intern)
    return Report(
        key="individual_timesheet",
        title=f"Individual OJT Timesheet - {intern.full_name} ({start} to {end})",
        columns=[
            "Date", "Day", "Shift", "Time In", "Time Out", "Scheduled",
            "Actual Rendered", "Creditable", "Late (min)", "Undertime (min)",
            "Overtime (min)", "Status", "Remarks",
        ],
        rows=rows,
        meta={
            "intern_code": intern.intern_code,
            "intern_name": intern.full_name,
            "school": intern.school.name if intern.school else None,
            "course": intern.course,
            "team": intern.team.name if intern.team else None,
            "supervisor": (
                intern.supervisor.user.full_name
                if intern.supervisor and intern.supervisor.user else None
            ),
            "required_hours": minutes_to_hours(progress.required_minutes),
            "approved_hours": minutes_to_hours(progress.approved_minutes),
            "remaining_hours": minutes_to_hours(progress.remaining_minutes),
            "completion_percentage": progress.completion_percentage,
        },
    )


def hours_rendered(db, start, end, **kw) -> Report:
    interns = _scoped_interns(db, **kw)
    rows = []
    for i in interns:
        period = db.scalar(
            select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
                OJTHourLedger.intern_id == i.id,
                OJTHourLedger.is_approved.is_(True),
                OJTHourLedger.is_reversed.is_(False),
                OJTHourLedger.reversal_of_id.is_(None),
                OJTHourLedger.business_date >= start,
                OJTHourLedger.business_date <= end,
            )
        ) or 0
        p = get_progress(db, i)
        rows.append([
            i.intern_code, i.display_name, i.team.name if i.team else "",
            minutes_to_hours(int(period)),
            minutes_to_hours(p.approved_minutes),
            minutes_to_hours(p.pending_minutes),
            minutes_to_hours(p.actual_rendered_minutes),
            minutes_to_hours(p.required_minutes),
            minutes_to_hours(p.remaining_minutes),
            p.completion_percentage,
        ])
    return Report(
        key="hours_rendered",
        title=f"OJT Hours Rendered Report ({start} to {end})",
        columns=[
            "Intern Code", "Name", "Team", "Hours in Period", "Total Approved",
            "Pending Approval", "Actual Rendered", "Required", "Remaining",
            "Completion %",
        ],
        rows=rows,
        numeric_columns=[3, 4, 5, 6, 7, 8, 9],
        meta={"date_from": str(start), "date_to": str(end)},
    )


def remaining_hours(db, start, end, **kw) -> Report:
    interns = _scoped_interns(db, **kw)
    rows = []
    for i in interns:
        p = get_progress(db, i)
        if p.is_complete:
            continue
        fc = forecast_service.forecast_for_intern(db, i, progress=p)
        rows.append([
            i.intern_code, i.display_name, i.team.name if i.team else "",
            minutes_to_hours(p.required_minutes),
            minutes_to_hours(p.approved_minutes),
            minutes_to_hours(p.remaining_minutes),
            p.completion_percentage,
            minutes_to_hours(fc.avg_minutes_per_week),
            str(fc.projected_completion_date or ""),
            str(i.school_submission_deadline or ""),
            fc.risk,
            fc.days_late or "",
        ])
    rows.sort(key=lambda r: -r[5])
    return Report(
        key="remaining_hours",
        title="Remaining OJT Hours Report",
        columns=[
            "Intern Code", "Name", "Team", "Required", "Approved", "Remaining",
            "Completion %", "Avg Hrs/Week", "Projected Completion",
            "School Deadline", "Risk", "Days Late",
        ],
        rows=rows,
        numeric_columns=[3, 4, 5, 6, 7],
        meta={"generated": str(utc_now())},
    )


def _exception_report(db, start, end, key, title, statuses=None, predicate=None, **kw) -> Report:
    interns = _scoped_interns(db, **kw)
    by_id = {i.id: i for i in interns}
    rows = []
    for r in _attendance_rows(db, interns, start, end, statuses):
        if predicate and not predicate(r):
            continue
        i = by_id[r.intern_id]
        rows.append([
            r.business_date.isoformat(), i.intern_code, i.display_name,
            i.team.name if i.team else "",
            (i.supervisor.user.full_name
             if i.supervisor and i.supervisor.user else ""),
            r.shift_code or "", _hhmm_time(r.time_in), _hhmm_time(r.time_out),
            r.late_minutes, r.undertime_minutes, r.overtime_minutes,
            r.approved_overtime_minutes, r.status, r.approval_status,
            r.supervisor_remarks or "",
        ])
    return Report(
        key=key, title=f"{title} ({start} to {end})",
        columns=[
            "Date", "Intern Code", "Name", "Team", "Supervisor", "Shift",
            "Time In", "Time Out", "Late (min)", "Undertime (min)",
            "Overtime (min)", "Approved OT (min)", "Status", "Approval", "Remarks",
        ],
        rows=rows,
        numeric_columns=[8, 9, 10, 11],
        meta={"date_from": str(start), "date_to": str(end), "count": len(rows)},
    )


def absenteeism(db, start, end, **kw) -> Report:
    return _exception_report(
        db, start, end, "absenteeism", "Absenteeism Report",
        statuses=(AttendanceStatus.ABSENT,), **kw
    )


def tardiness(db, start, end, **kw) -> Report:
    return _exception_report(
        db, start, end, "tardiness", "Tardiness Report",
        predicate=lambda r: r.is_late, **kw
    )


def undertime(db, start, end, **kw) -> Report:
    return _exception_report(
        db, start, end, "undertime", "Undertime Report",
        predicate=lambda r: r.undertime_minutes > 0, **kw
    )


def overtime(db, start, end, **kw) -> Report:
    return _exception_report(
        db, start, end, "overtime", "Overtime Report",
        predicate=lambda r: r.overtime_minutes > 0, **kw
    )


def attendance_exceptions(db, start, end, **kw) -> Report:
    return _exception_report(
        db, start, end, "attendance_exceptions", "Attendance Exceptions Report",
        predicate=lambda r: (
            r.status in (
                AttendanceStatus.ABSENT, AttendanceStatus.INCOMPLETE_TIME_RECORD,
                AttendanceStatus.HALF_DAY,
            )
            or r.is_late or r.undertime_minutes > 0
            or (r.overtime_minutes > 0 and not r.overtime_approved)
            or r.approval_status == ApprovalStatus.PENDING
        ),
        **kw
    )


def completion_monitoring(db, start, end, **kw) -> Report:
    interns = _scoped_interns(db, include_inactive=True, **{
        k: v for k, v in kw.items() if k != "include_inactive"
    })
    rows = []
    for i in interns:
        p = get_progress(db, i)
        fc = forecast_service.forecast_for_intern(db, i, progress=p)
        rows.append([
            i.intern_code, i.display_name, i.status,
            i.school.name if i.school else "", i.course or "",
            i.ojt_start_date.isoformat(),
            str(i.expected_completion_date or ""),
            str(i.school_submission_deadline or ""),
            minutes_to_hours(p.required_minutes),
            minutes_to_hours(p.approved_minutes),
            minutes_to_hours(p.remaining_minutes),
            p.completion_percentage, p.milestone or "",
            str(fc.projected_completion_date or ""), fc.risk,
            str(i.actual_completion_date or ""),
        ])
    return Report(
        key="completion_monitoring", title="Intern Completion Monitoring",
        columns=[
            "Intern Code", "Name", "Status", "School", "Course", "OJT Start",
            "Expected Completion", "School Deadline", "Required", "Approved",
            "Remaining", "Completion %", "Milestone", "Projected", "Risk",
            "Actual Completion",
        ],
        rows=rows,
        numeric_columns=[8, 9, 10, 11],
    )


def schedule_compliance(db, start, end, **kw) -> Report:
    """Scheduled versus actual attendance, per intern."""
    interns = _scoped_interns(db, **kw)
    rows = []
    for i in interns:
        schedule = resolve_range(db, i, start, end)
        scheduled_days = sum(1 for r in schedule.values() if r.is_working_day)
        scheduled_minutes = sum(
            r.scheduled_net_minutes for r in schedule.values() if r.is_working_day
        )
        recs = _attendance_rows(db, [i], start, end)
        reported = sum(
            1 for r in recs if r.status in dashboard_service.PRESENT_STATUSES
        )
        actual_minutes = sum(r.actual_net_minutes for r in recs)
        absent = sum(1 for r in recs if r.status == AttendanceStatus.ABSENT)
        rows.append([
            i.intern_code, i.display_name, i.team.name if i.team else "",
            scheduled_days, reported, absent,
            minutes_to_hours(scheduled_minutes), minutes_to_hours(actual_minutes),
            round(reported / scheduled_days * 100, 2) if scheduled_days else 0.0,
            round(actual_minutes / scheduled_minutes * 100, 2)
            if scheduled_minutes else 0.0,
        ])
    return Report(
        key="schedule_compliance",
        title=f"Schedule Compliance Report ({start} to {end})",
        columns=[
            "Intern Code", "Name", "Team", "Scheduled Days", "Days Reported",
            "Days Absent", "Scheduled Hours", "Actual Hours",
            "Day Compliance %", "Hour Compliance %",
        ],
        rows=rows,
        numeric_columns=[3, 4, 5, 6, 7, 8, 9],
        meta={"date_from": str(start), "date_to": str(end)},
    )


def supervisor_monitoring(db, start, end, **kw) -> Report:
    rules = load_rules(db)
    backlog = dashboard_service.approval_backlog(db, rules=rules)
    teams = dashboard_service.attendance_by_team(db, start, end, rules=rules)
    team_by_name = {t["team_name"]: t for t in teams}

    rows = []
    for s in backlog["supervisors"]:
        rows.append([
            s["supervisor_name"], s["intern_count"], s["pending_attendance"],
            s["pending_corrections"], s["pending_leave"], s["pending_timesheets"],
            s["total_pending"], s["overdue_items"],
            s["oldest_pending_days"] if s["oldest_pending_days"] is not None else "",
            s["status"],
        ])
    return Report(
        key="supervisor_monitoring",
        title=f"Supervisor Monitoring Report ({start} to {end})",
        columns=[
            "Supervisor", "Interns", "Pending Attendance", "Pending Corrections",
            "Pending Leave", "Pending Timesheets", "Total Pending", "Overdue",
            "Oldest Pending (days)", "Status",
        ],
        rows=rows,
        numeric_columns=[1, 2, 3, 4, 5, 6, 7],
        meta={"warning_threshold_days": backlog["warning_threshold_days"]},
    )


def technical_exposure(db, start, end, **kw) -> Report:
    mx = exposure_service.exposure_matrix(
        db, team_id=kw.get("team_id"), supervisor_id=kw.get("supervisor_id"),
        date_from=start, date_to=end,
    )
    cols = ["Intern Code", "Name", "Team"] + [
        exposure_service.AREA_LABELS.get(a, a) for a in mx["area_keys"]
    ] + ["Total Hours", "Core Areas Met", "Gaps"]
    rows = []
    for r in mx["interns"]:
        rows.append(
            [r["intern_code"], r["intern_name"], r["team"] or ""]
            + [r["areas"][a]["hours"] for a in mx["area_keys"]]
            + [r["total_logged_hours"],
               f"{r['core_areas_met']}/{r['core_areas_total']}",
               ", ".join(r["gaps"])]
        )
    return Report(
        key="technical_exposure",
        title=f"Technical Exposure Report ({start} to {end})",
        columns=cols, rows=rows,
        numeric_columns=list(range(3, 3 + len(mx["area_keys"]) + 1)),
        meta={"department_gaps": [g["label"] for g in mx["department_gaps"]]},
    )


REPORTS: dict[str, tuple[str, callable]] = {
    "daily_attendance": ("Daily Attendance Report", daily_attendance),
    "weekly_summary": ("Weekly Attendance Summary", weekly_summary),
    "monthly_summary": ("Monthly Attendance Summary", monthly_summary),
    "individual_timesheet": ("Individual OJT Timesheet", individual_timesheet),
    "hours_rendered": ("OJT Hours Rendered Report", hours_rendered),
    "remaining_hours": ("Remaining OJT Hours Report", remaining_hours),
    "absenteeism": ("Absenteeism Report", absenteeism),
    "tardiness": ("Tardiness Report", tardiness),
    "undertime": ("Undertime Report", undertime),
    "overtime": ("Overtime Report", overtime),
    "completion_monitoring": ("Intern Completion Monitoring", completion_monitoring),
    "schedule_compliance": ("Schedule Compliance Report", schedule_compliance),
    "supervisor_monitoring": ("Supervisor Monitoring Report", supervisor_monitoring),
    "attendance_exceptions": ("Attendance Exceptions Report", attendance_exceptions),
    "technical_exposure": ("Technical Exposure Report", technical_exposure),
}


def build(
    db: Session, key: str, start: date, end: date, **kwargs
) -> Report:
    if key not in REPORTS:
        raise ValueError(
            f"Unknown report '{key}'. Available: {', '.join(sorted(REPORTS))}"
        )
    _, builder = REPORTS[key]
    return builder(db, start, end, **kwargs)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def to_csv(report: Report) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([report.title])
    for k, v in report.meta.items():
        writer.writerow([f"{k}:", str(v)])
    writer.writerow([])
    writer.writerow(report.columns)
    writer.writerows(report.rows)
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(report: Report, rules: RuleSet | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    org = rules.str_("organization_name") if rules else "IT Department"
    company = rules.str_("company_name") if rules else ""

    wb = Workbook()
    ws = wb.active
    ws.title = report.key[:31]

    header_fill = PatternFill("solid", fgColor="1F2A3A")
    header_font = Font(bold=True, color="FFFFFF", size=10)
    title_font = Font(bold=True, size=14)
    thin = Side(style="thin", color="D0D4DA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    row_i = 1
    ws.cell(row=row_i, column=1, value=company or org).font = title_font
    row_i += 1
    if company:
        ws.cell(row=row_i, column=1, value=org).font = Font(bold=True, size=11)
        row_i += 1
    ws.cell(row=row_i, column=1, value=report.title).font = Font(bold=True, size=12)
    row_i += 1
    ws.cell(
        row=row_i, column=1,
        value=f"Generated {to_business(utc_now()).strftime('%Y-%m-%d %H:%M %Z')}",
    ).font = Font(italic=True, size=9)
    row_i += 1
    for k, v in report.meta.items():
        if isinstance(v, (str, int, float)):
            ws.cell(row=row_i, column=1, value=f"{k.replace('_', ' ').title()}: {v}")
            row_i += 1
    row_i += 1

    header_row = row_i
    for c, name in enumerate(report.columns, start=1):
        cell = ws.cell(row=header_row, column=c, value=name)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")

    for r, data in enumerate(report.rows, start=header_row + 1):
        for c, value in enumerate(data, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = border
            if isinstance(value, (int, float)):
                cell.alignment = Alignment(horizontal="right")

    # Totals for numeric columns.
    if report.rows and report.numeric_columns:
        total_row = header_row + len(report.rows) + 1
        ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)
        for idx in report.numeric_columns:
            col = idx + 1
            if col > len(report.columns):
                continue
            letter = get_column_letter(col)
            cell = ws.cell(
                row=total_row, column=col,
                value=f"=SUM({letter}{header_row + 1}:{letter}{total_row - 1})",
            )
            cell.font = Font(bold=True)
            cell.border = border

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = (
        f"A{header_row}:"
        f"{get_column_letter(len(report.columns))}{header_row + len(report.rows)}"
    )
    for c, name in enumerate(report.columns, start=1):
        longest = max(
            [len(str(name))] +
            [len(str(r[c - 1])) for r in report.rows[:400] if c - 1 < len(r)]
            or [10]
        )
        ws.column_dimensions[get_column_letter(c)].width = min(42, max(11, longest + 2))

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_pdf(report: Report, rules: RuleSet | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    org = rules.str_("organization_name") if rules else "IT Department"
    company = rules.str_("company_name") if rules else ""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=12 * mm,
        title=report.title,
    )
    styles = getSampleStyleSheet()
    h_center = ParagraphStyle("hc", parent=styles["Heading2"], alignment=TA_CENTER)
    small = ParagraphStyle("sm", parent=styles["Normal"], fontSize=7, leading=8.5)
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=6.5, leading=8)

    story = []
    if company:
        story.append(Paragraph(f"<b>{company}</b>", h_center))
    story.append(Paragraph(org, h_center))
    story.append(Paragraph(f"<b>{report.title}</b>", h_center))
    story.append(Spacer(1, 3 * mm))

    meta_bits = [
        f"{k.replace('_', ' ').title()}: {v}"
        for k, v in report.meta.items()
        if isinstance(v, (str, int, float))
    ]
    meta_bits.append(
        f"Generated: {to_business(utc_now()).strftime('%Y-%m-%d %H:%M %Z')}"
    )
    story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_bits), small))
    story.append(Spacer(1, 3 * mm))

    if not report.rows:
        story.append(Paragraph("No records matched the selected filters.", styles["Normal"]))
    else:
        head = [Paragraph(f"<b>{c}</b>", cell_style) for c in report.columns]
        body = [
            [Paragraph(str(v) if v is not None else "", cell_style) for v in row]
            for row in report.rows[:3000]
        ]
        table = Table([head] + body, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2A3A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B9BFC8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F4F6F8")]),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(table)
        if len(report.rows) > 3000:
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(
                f"Showing the first 3,000 of {len(report.rows)} rows. "
                f"Export to Excel or CSV for the complete set.", small
            ))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "Prepared by: ____________________ &nbsp;&nbsp;&nbsp; "
        "Reviewed by: ____________________ &nbsp;&nbsp;&nbsp; "
        "Date: ____________", small
    ))

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(
            landscape(A4)[0] - 10 * mm, 7 * mm, f"Page {doc_.page}"
        )
        canvas.drawString(10 * mm, 7 * mm, report.title)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
