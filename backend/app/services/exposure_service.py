"""Technical Exposure Tracker.

Attendance proves an intern was present. This proves what they actually did.
The distinction matters for OJT: a school signs off on hours, but the intern
needs demonstrable experience across the IT functions, and a supervisor needs
to see who has never touched Active Directory three weeks before the
internship ends.

Exposure hours are deliberately INDEPENDENT of the OJT hour ledger. Logging an
activity never credits attendance, and crediting attendance never invents
exposure. They answer different questions and are reconciled only in reports,
where the coverage ratio (exposure logged / hours rendered) shows how much of
an intern's time is accounted for by recorded activity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import ExposureArea, InternStatus, ProficiencyLevel
from app.core.timeutil import minutes_to_hours, utc_now
from app.models.intern import Intern
from app.models.ojt import OJTHourLedger
from app.models.technical import ExposureLog, ExposureTarget
from app.services.settings_service import RuleSet, load_rules

# Human labels for the taxonomy, used in reports and the UI.
AREA_LABELS: dict[str, str] = {
    ExposureArea.DESKTOP_TROUBLESHOOTING: "Desktop Troubleshooting",
    ExposureArea.AD_ADMINISTRATION: "Active Directory Administration",
    ExposureArea.NETWORKING: "Networking",
    ExposureArea.HARDWARE_DEPLOYMENT: "Hardware Deployment",
    ExposureArea.PATCHING: "Patching & Updates",
    ExposureArea.DOCUMENTATION: "Technical Documentation",
    ExposureArea.PROJECTS: "IT Projects",
    ExposureArea.SERVICE_DESK_HANDLING: "Service Desk Handling",
    ExposureArea.SECURITY_COMPLIANCE: "Security & Compliance",
    ExposureArea.BACKUP_RECOVERY: "Backup & Recovery",
}

# The seven areas the department treats as core, in the order they are
# reported. Others are tracked but not required by default.
CORE_AREAS: list[str] = [
    ExposureArea.DESKTOP_TROUBLESHOOTING,
    ExposureArea.AD_ADMINISTRATION,
    ExposureArea.NETWORKING,
    ExposureArea.HARDWARE_DEPLOYMENT,
    ExposureArea.PATCHING,
    ExposureArea.DOCUMENTATION,
    ExposureArea.PROJECTS,
]

PROFICIENCY_RANK: dict[str, int] = {
    ProficiencyLevel.OBSERVED: 1,
    ProficiencyLevel.ASSISTED: 2,
    ProficiencyLevel.PERFORMED_SUPERVISED: 3,
    ProficiencyLevel.PERFORMED_INDEPENDENT: 4,
}

# Default target hours per core area when no ExposureTarget row exists.
DEFAULT_TARGET_HOURS = 8


class ExposureError(Exception):
    def __init__(self, message: str, code: str = "EXPOSURE_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


def log_activity(
    db: Session,
    intern: Intern,
    *,
    activity_date: date,
    exposure_area: str,
    activity_title: str,
    minutes_spent: int,
    logged_by_id: int,
    description: str | None = None,
    proficiency_level: str = ProficiencyLevel.ASSISTED,
    reference_ticket: str | None = None,
    project_name: str | None = None,
    asset_tag: str | None = None,
) -> ExposureLog:
    """Record one hands-on activity."""
    if minutes_spent < 0:
        raise ExposureError("Minutes spent cannot be negative.", code="INVALID_MINUTES")
    if activity_date < intern.ojt_start_date:
        raise ExposureError(
            f"Activity date {activity_date} is before the intern's OJT start "
            f"date ({intern.ojt_start_date}).",
            code="DATE_BEFORE_START",
        )
    if activity_date > date.today() + timedelta(days=1):
        raise ExposureError(
            "Activity date cannot be in the future.", code="FUTURE_DATE"
        )

    entry = ExposureLog(
        intern_id=intern.id,
        activity_date=activity_date,
        exposure_area=exposure_area,
        activity_title=activity_title,
        description=description,
        minutes_spent=minutes_spent,
        proficiency_level=proficiency_level,
        reference_ticket=reference_ticket,
        project_name=project_name,
        asset_tag=asset_tag,
        logged_by_id=logged_by_id,
        created_by_id=logged_by_id,
    )
    db.add(entry)
    db.flush()
    return entry


def verify_activity(
    db: Session, entry: ExposureLog, *, verifier_id: int, feedback: str | None = None
) -> ExposureLog:
    """Mentor or supervisor confirms the activity happened as logged."""
    intern = db.get(Intern, entry.intern_id)
    if intern and intern.user_id == verifier_id:
        raise ExposureError(
            "An intern cannot verify their own exposure log.",
            code="SELF_VERIFY_FORBIDDEN",
        )
    entry.verified_by_id = verifier_id
    entry.verified_at = utc_now()
    if feedback:
        entry.mentor_feedback = feedback
    db.flush()
    return entry


def _targets_for(db: Session, intern: Intern) -> dict[str, int]:
    """Resolve target minutes per area; most specific scope wins."""
    rows = db.scalars(
        select(ExposureTarget).where(ExposureTarget.is_deleted.is_(False))
    ).all()

    resolved: dict[str, tuple[int, int]] = {}  # area -> (specificity, minutes)
    for t in rows:
        if t.intern_id is not None and t.intern_id != intern.id:
            continue
        if t.team_id is not None and t.team_id != intern.team_id:
            continue
        specificity = 2 if t.intern_id else (1 if t.team_id else 0)
        cur = resolved.get(t.exposure_area)
        if cur is None or specificity > cur[0]:
            resolved[t.exposure_area] = (specificity, t.target_minutes)

    out = {area: DEFAULT_TARGET_HOURS * 60 for area in CORE_AREAS}
    for area, (_, minutes) in resolved.items():
        out[area] = minutes
    return out


@dataclass
class AreaCoverage:
    area: str
    label: str
    logged_minutes: int
    verified_minutes: int
    target_minutes: int
    activity_count: int
    best_proficiency: str | None
    last_activity_date: date | None
    is_core: bool

    @property
    def coverage_percentage(self) -> float:
        if self.target_minutes <= 0:
            return 100.0 if self.logged_minutes > 0 else 0.0
        return round(min(100.0, self.logged_minutes / self.target_minutes * 100), 1)

    @property
    def is_met(self) -> bool:
        return self.logged_minutes >= self.target_minutes

    @property
    def is_untouched(self) -> bool:
        return self.activity_count == 0

    def as_dict(self) -> dict:
        return {
            "area": self.area,
            "label": self.label,
            "logged_hours": minutes_to_hours(self.logged_minutes),
            "verified_hours": minutes_to_hours(self.verified_minutes),
            "target_hours": minutes_to_hours(self.target_minutes),
            "coverage_percentage": self.coverage_percentage,
            "activity_count": self.activity_count,
            "best_proficiency": self.best_proficiency,
            "last_activity_date": (
                self.last_activity_date.isoformat() if self.last_activity_date else None
            ),
            "is_core": self.is_core,
            "is_met": self.is_met,
            "is_untouched": self.is_untouched,
        }


def intern_exposure(
    db: Session,
    intern: Intern,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Per-intern exposure matrix with gaps called out."""
    stmt = select(ExposureLog).where(
        ExposureLog.intern_id == intern.id,
        ExposureLog.is_deleted.is_(False),
    )
    if date_from:
        stmt = stmt.where(ExposureLog.activity_date >= date_from)
    if date_to:
        stmt = stmt.where(ExposureLog.activity_date <= date_to)
    logs = list(db.scalars(stmt).all())

    targets = _targets_for(db, intern)
    all_areas = list(dict.fromkeys(CORE_AREAS + [a.value for a in ExposureArea]))

    coverage: list[AreaCoverage] = []
    for area in all_areas:
        area_logs = [l for l in logs if l.exposure_area == area]
        target = targets.get(area, 0)
        if not area_logs and area not in CORE_AREAS and target == 0:
            continue  # never touched and not required: omit from the matrix
        best = None
        if area_logs:
            best = max(
                area_logs, key=lambda l: PROFICIENCY_RANK.get(l.proficiency_level, 0)
            ).proficiency_level
        coverage.append(
            AreaCoverage(
                area=area,
                label=AREA_LABELS.get(area, area.replace("_", " ").title()),
                logged_minutes=sum(l.minutes_spent for l in area_logs),
                verified_minutes=sum(
                    l.minutes_spent for l in area_logs if l.verified_by_id is not None
                ),
                target_minutes=target,
                activity_count=len(area_logs),
                best_proficiency=best,
                last_activity_date=max((l.activity_date for l in area_logs), default=None),
                is_core=area in CORE_AREAS,
            )
        )

    total_logged = sum(c.logged_minutes for c in coverage)
    total_verified = sum(c.verified_minutes for c in coverage)
    core = [c for c in coverage if c.is_core]
    gaps = [c for c in core if c.is_untouched]
    below = [c for c in core if not c.is_untouched and not c.is_met]

    # Rendered hours from the ledger, for the coverage ratio.
    rendered = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            OJTHourLedger.intern_id == intern.id,
            OJTHourLedger.is_reversed.is_(False),
            OJTHourLedger.reversal_of_id.is_(None),
            OJTHourLedger.is_approved.is_(True),
        )
    ) or 0

    return {
        "intern_id": intern.id,
        "intern_code": intern.intern_code,
        "intern_name": intern.display_name,
        "team": intern.team.name if intern.team else None,
        "primary_technical_function": intern.primary_technical_function,
        "period": {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
        },
        "total_logged_hours": minutes_to_hours(total_logged),
        "total_verified_hours": minutes_to_hours(total_verified),
        "verified_ratio": (
            round(total_verified / total_logged * 100, 1) if total_logged else 0.0
        ),
        "ojt_hours_rendered": minutes_to_hours(int(rendered)),
        # How much of the intern's credited time has a recorded activity behind
        # it. Below 100% is normal (not every minute is logged); very low
        # suggests activity logging is not being kept up.
        "activity_coverage_ratio": (
            round(total_logged / rendered * 100, 1) if rendered else 0.0
        ),
        "core_areas_total": len(core),
        "core_areas_met": sum(1 for c in core if c.is_met),
        "core_areas_untouched": len(gaps),
        "coverage": [c.as_dict() for c in coverage],
        "gaps": [c.label for c in gaps],
        "below_target": [
            {"label": c.label, "logged_hours": minutes_to_hours(c.logged_minutes),
             "target_hours": minutes_to_hours(c.target_minutes)}
            for c in below
        ],
        "recent_activities": [
            {
                "id": l.id,
                "date": l.activity_date.isoformat(),
                "area": AREA_LABELS.get(l.exposure_area, l.exposure_area),
                "title": l.activity_title,
                "hours": minutes_to_hours(l.minutes_spent),
                "proficiency": l.proficiency_level,
                "ticket": l.reference_ticket,
                "project": l.project_name,
                "verified": l.verified_by_id is not None,
            }
            for l in sorted(logs, key=lambda x: x.activity_date, reverse=True)[:15]
        ],
    }


def exposure_matrix(
    db: Session,
    *,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Cross-intern coverage grid: who has touched what.

    This is the view a supervisor uses to rotate assignments - the columns with
    the most blanks are the areas to staff next.
    """
    stmt = select(Intern).where(
        Intern.is_deleted.is_(False),
        Intern.status.in_([InternStatus.ACTIVE, InternStatus.ON_LEAVE]),
    )
    if team_id:
        stmt = stmt.where(Intern.team_id == team_id)
    if supervisor_id:
        stmt = stmt.where(Intern.supervisor_id == supervisor_id)
    interns = list(db.scalars(stmt).all())

    rows: list[dict] = []
    area_totals: dict[str, dict] = {
        a: {"area": a, "label": AREA_LABELS.get(a, a), "interns_with_exposure": 0,
            "total_hours": 0.0, "interns_untouched": 0}
        for a in CORE_AREAS
    }

    for intern in interns:
        detail = intern_exposure(db, intern, date_from=date_from, date_to=date_to)
        cov = {c["area"]: c for c in detail["coverage"]}
        row = {
            "intern_id": intern.id,
            "intern_code": intern.intern_code,
            "intern_name": intern.display_name,
            "team": detail["team"],
            "total_logged_hours": detail["total_logged_hours"],
            "core_areas_met": detail["core_areas_met"],
            "core_areas_total": detail["core_areas_total"],
            "gaps": detail["gaps"],
            "areas": {},
        }
        for a in CORE_AREAS:
            c = cov.get(a)
            hours = c["logged_hours"] if c else 0.0
            row["areas"][a] = {
                "hours": hours,
                "coverage_percentage": c["coverage_percentage"] if c else 0.0,
                "proficiency": c["best_proficiency"] if c else None,
            }
            area_totals[a]["total_hours"] += hours
            if hours > 0:
                area_totals[a]["interns_with_exposure"] += 1
            else:
                area_totals[a]["interns_untouched"] += 1
        rows.append(row)

    for a in area_totals.values():
        a["total_hours"] = round(a["total_hours"], 2)
        a["exposure_rate"] = (
            round(a["interns_with_exposure"] / len(interns) * 100, 1) if interns else 0.0
        )

    rows.sort(key=lambda r: (r["core_areas_met"], r["total_logged_hours"]))

    return {
        "generated_at": utc_now().isoformat(),
        "areas": [AREA_LABELS.get(a, a) for a in CORE_AREAS],
        "area_keys": CORE_AREAS,
        "area_summary": list(area_totals.values()),
        "interns": rows,
        "intern_count": len(interns),
        # Areas where most interns have no exposure at all: the staffing gaps.
        "department_gaps": sorted(
            [a for a in area_totals.values() if a["exposure_rate"] < 50],
            key=lambda a: a["exposure_rate"],
        ),
    }
