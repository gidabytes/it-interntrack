"""Configurable computation rules.

The spec requires every attendance / OJT computation rule to be configurable.
Each rule has a code default here and is persisted in ``system_settings`` on
first run, where an administrator can change it without a deploy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import SystemSetting

# key -> (default, type, category, description, editable)
DEFAULT_SETTINGS: dict[str, tuple[Any, str, str, str, bool]] = {
    # --- Crediting policy ---------------------------------------------------
    "max_creditable_minutes_per_day": (
        600, "int", "OJT_COMPUTATION",
        "Hard ceiling on creditable OJT minutes for a single business date.",
        True,
    ),
    "min_creditable_minutes": (
        1, "int", "OJT_COMPUTATION",
        "Sessions shorter than this credit zero minutes.",
        True,
    ),
    "credit_requires_approval": (
        True, "bool", "OJT_COMPUTATION",
        "Only supervisor-approved attendance counts toward approved OJT hours.",
        True,
    ),
    "cap_completion_at_100": (
        True, "bool", "OJT_COMPUTATION",
        "Approved hours never exceed required hours in the completion figure.",
        True,
    ),
    "count_overtime_toward_ojt": (
        True, "bool", "OJT_COMPUTATION",
        "Approved overtime is creditable toward OJT hours.",
        True,
    ),
    "count_unapproved_overtime": (
        False, "bool", "OJT_COMPUTATION",
        "Unauthorised overtime is never credited when false.",
        True,
    ),
    "credit_work_before_shift_start": (
        False, "bool", "OJT_COMPUTATION",
        "Time worked before the scheduled start credits only as approved overtime.",
        True,
    ),
    "approved_leave_counts_as_hours": (
        False, "bool", "OJT_COMPUTATION",
        "Approved leave does not credit OJT hours unless explicitly enabled.",
        True,
    ),
    "holiday_counts_as_hours": (
        False, "bool", "OJT_COMPUTATION",
        "Non-working holidays do not credit OJT hours unless enabled.",
        True,
    ),

    # --- Break policy ------------------------------------------------------
    "auto_deduct_unpaid_break": (
        True, "bool", "BREAK_POLICY",
        "When no break was logged, deduct the shift's scheduled unpaid break.",
        True,
    ),
    "deduct_actual_break_when_longer": (
        True, "bool", "BREAK_POLICY",
        "If logged breaks exceed the scheduled unpaid break, deduct the actual time.",
        True,
    ),
    "max_break_minutes_per_session": (
        180, "int", "BREAK_POLICY",
        "Sanity ceiling on total break minutes in one session.",
        True,
    ),

    # --- Attendance thresholds ---------------------------------------------
    "default_grace_period_minutes": (
        15, "int", "ATTENDANCE_RULES",
        "Fallback late grace period when the shift template does not set one.",
        True,
    ),
    "grace_reduces_late_minutes": (
        False, "bool", "ATTENDANCE_RULES",
        "When true, late minutes are measured from the end of the grace period "
        "instead of the scheduled start.",
        True,
    ),
    "absence_finalize_grace_minutes": (
        60, "int", "ATTENDANCE_RULES",
        "Minutes after a shift ends before a no-show is finalised as ABSENT.",
        True,
    ),
    "half_day_threshold_ratio": (
        0.5, "float", "ATTENDANCE_RULES",
        "Net worked / scheduled net at or below this ratio is HALF_DAY.",
        True,
    ),
    "grace_forgives_undertime": (
        True, "bool", "ATTENDANCE_RULES",
        "When an arrival falls within the late grace period, the few minutes "
        "missed are not reported as undertime. Without this the grace period is "
        "meaningless: every intern who used it would be flagged UNDERTIME. "
        "Creditable minutes still follow time actually worked, so no hours are "
        "given away - only the exception flag is suppressed.",
        True,
    ),
    "undertime_tolerance_minutes": (
        5, "int", "ATTENDANCE_RULES",
        "Undertime below this many minutes is ignored.",
        True,
    ),
    "early_time_in_window_minutes": (
        120, "int", "ATTENDANCE_RULES",
        "How early before the scheduled start a time-in still binds to that shift.",
        True,
    ),
    "late_time_in_window_minutes": (
        480, "int", "ATTENDANCE_RULES",
        "How late after the scheduled start a time-in still binds to that shift.",
        True,
    ),
    "max_session_minutes": (
        1080, "int", "ATTENDANCE_RULES",
        "Sessions longer than this are flagged as unusual working hours.",
        True,
    ),
    "allow_attendance_without_schedule": (
        True, "bool", "ATTENDANCE_RULES",
        "Permit time-in with no assigned shift, recorded as NO_SCHEDULE.",
        True,
    ),
    "auto_close_missing_time_out": (
        False, "bool", "ATTENDANCE_RULES",
        "Auto-close an open session at its scheduled end instead of leaving it "
        "as INCOMPLETE_TIME_RECORD. Off by default: a supervisor should decide.",
        True,
    ),

    # --- Schedule validation -----------------------------------------------
    "min_rest_hours_between_shifts": (
        8, "int", "SCHEDULE_RULES",
        "Minimum rest between the end of one shift and the start of the next.",
        True,
    ),
    "block_on_schedule_conflict": (
        True, "bool", "SCHEDULE_RULES",
        "Reject conflicting assignments unless a supervisor authorises the exception.",
        True,
    ),
    "require_approval_for_schedule_change": (
        True, "bool", "SCHEDULE_RULES",
        "Schedule modifications need supervisor approval before taking effect.",
        True,
    ),

    # --- Forecast ----------------------------------------------------------
    "forecast_lookback_weeks": (
        4, "int", "FORECAST",
        "Weeks of approved history used to compute the average weekly pace.",
        True,
    ),
    "forecast_at_risk_buffer_days": (
        7, "int", "FORECAST",
        "Projected completion within this many days of the school deadline is AT_RISK.",
        True,
    ),
    "completion_warning_percentage": (
        90.0, "float", "FORECAST",
        "Completion at or above this percentage raises an approaching-completion alert.",
        True,
    ),

    # --- Compliance --------------------------------------------------------
    "attendance_compliance_target": (
        95.0, "float", "COMPLIANCE",
        "Target attendance compliance percentage for dashboard status.",
        True,
    ),
    "excessive_absence_threshold": (
        3, "int", "COMPLIANCE",
        "Absences within the review window that trigger an alert.",
        True,
    ),
    "absence_review_window_days": (
        30, "int", "COMPLIANCE",
        "Rolling window for the excessive-absence check.",
        True,
    ),
    "approval_backlog_warning_days": (
        3, "int", "COMPLIANCE",
        "Pending approvals older than this are counted as backlog.",
        True,
    ),

    # --- Organisation ------------------------------------------------------
    "default_required_hours": (
        486, "int", "GENERAL",
        "Default required OJT hours for a new intern.",
        True,
    ),
    "organization_name": (
        "Information Technology Department", "str", "GENERAL",
        "Printed on reports and timesheets.",
        True,
    ),
    "company_name": (
        "Company Name", "str", "GENERAL",
        "Printed on reports and timesheets.",
        True,
    ),
}


def _coerce(raw: str, value_type: str) -> Any:
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if value_type == "json":
        return json.loads(raw)
    return raw


def _serialize(value: Any, value_type: str) -> str:
    if value_type == "json":
        return json.dumps(value)
    if value_type == "bool":
        return "true" if value else "false"
    return str(value)


@dataclass
class RuleSet:
    """Resolved settings for one computation pass.

    Loaded once per operation so a long report does not re-query per row, and
    so the values used by a single computation are internally consistent.
    """

    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.values:
            return self.values[key]
        if key in DEFAULT_SETTINGS:
            return DEFAULT_SETTINGS[key][0]
        return default

    def int_(self, key: str) -> int:
        return int(self.get(key))

    def float_(self, key: str) -> float:
        return float(self.get(key))

    def bool_(self, key: str) -> bool:
        return bool(self.get(key))

    def str_(self, key: str) -> str:
        return str(self.get(key))


def ensure_defaults(db: Session) -> int:
    """Insert any missing settings rows. Idempotent; never overwrites values."""
    existing = {s.key for s in db.scalars(select(SystemSetting)).all()}
    created = 0
    for key, (default, vtype, category, desc, editable) in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        db.add(
            SystemSetting(
                key=key,
                value=_serialize(default, vtype),
                value_type=vtype,
                category=category,
                description=desc,
                is_editable=editable,
            )
        )
        created += 1
    if created:
        db.flush()
    return created


def load_rules(db: Session) -> RuleSet:
    """Read every setting, falling back to code defaults for missing keys."""
    values: dict[str, Any] = {
        k: v[0] for k, v in DEFAULT_SETTINGS.items()
    }
    for s in db.scalars(select(SystemSetting)).all():
        try:
            values[s.key] = _coerce(s.value, s.value_type)
        except (ValueError, json.JSONDecodeError):
            # A malformed stored value must not break attendance computation;
            # fall back to the code default.
            values[s.key] = DEFAULT_SETTINGS.get(s.key, (None,))[0]
    return RuleSet(values)


def set_setting(db: Session, key: str, value: Any, actor_user_id: int | None = None) -> SystemSetting:
    """Update a setting, creating it if the key is known but not yet stored."""
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if setting is None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(f"Unknown setting: {key}")
        _, vtype, category, desc, editable = DEFAULT_SETTINGS[key]
        setting = SystemSetting(
            key=key, value_type=vtype, category=category,
            description=desc, is_editable=editable, value="",
        )
        db.add(setting)
    if not setting.is_editable:
        raise PermissionError(f"Setting '{key}' is not editable")
    setting.value = _serialize(value, setting.value_type)
    setting.updated_by_id = actor_user_id
    db.flush()
    return setting
