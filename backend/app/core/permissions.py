"""Permission catalogue and role mapping (RBAC).

Permissions are fine-grained strings; roles are bundles of them. The API guards
on permissions rather than role names, so adding a role later does not mean
editing every endpoint.
"""
from __future__ import annotations

from app.core.enums import RoleName

# code -> description
PERMISSIONS: dict[str, str] = {
    # Users and system
    "user.manage": "Create, edit and deactivate user accounts",
    "role.manage": "Assign roles and permissions",
    "settings.manage": "Change system settings and computation rules",
    "audit.read": "View the audit trail",
    # Interns
    "intern.read": "View intern profiles",
    "intern.read.assigned": "View only assigned interns",
    "intern.read.self": "View own intern profile",
    "intern.manage": "Create, edit and archive intern profiles",
    "intern.export": "Export intern profiles",
    # Shifts and schedules
    "shift.read": "View shift templates",
    "shift.manage": "Create and edit shift templates",
    "schedule.read": "View schedules",
    "schedule.read.self": "View own schedule",
    "schedule.assign": "Assign schedules to interns",
    "schedule.approve": "Approve schedule modifications",
    "holiday.manage": "Manage holidays and rest days",
    # Attendance
    "attendance.read": "View attendance records",
    "attendance.read.assigned": "View assigned interns' attendance",
    "attendance.read.self": "View own attendance",
    "attendance.punch": "Record own time in and time out",
    "attendance.manage": "Record and correct attendance for others",
    "attendance.approve": "Approve attendance and corrections",
    "attendance.request_correction": "Submit an attendance correction request",
    "overtime.approve": "Approve overtime",
    # OJT hours
    "ojt.read": "View OJT progress",
    "ojt.read.self": "View own OJT progress",
    "ojt.adjust": "Post manual OJT hour adjustments",
    # Leave
    "leave.request": "Submit leave requests",
    "leave.read": "View leave requests",
    "leave.approve": "Approve or reject leave requests",
    # Timesheets
    "timesheet.read": "View timesheets",
    "timesheet.read.self": "View own timesheets",
    "timesheet.generate": "Generate timesheets",
    "timesheet.submit": "Submit own timesheet for review",
    "timesheet.sign": "Review and sign timesheets",
    # Technical exposure and evaluation
    "exposure.read": "View technical exposure records",
    "exposure.read.self": "View own technical exposure",
    "exposure.log": "Log technical exposure activities",
    "exposure.verify": "Verify logged activities",
    "evaluation.read": "View evaluations",
    "evaluation.manage": "Create and edit evaluations",
    # Dashboards and reports
    "dashboard.read": "View the management dashboard",
    "dashboard.compliance": "View the compliance dashboard",
    "dutyboard.read": "View the live duty board",
    "report.read": "Generate and view reports",
    "report.export": "Export reports to Excel, CSV and PDF",
}

_ALL = set(PERMISSIONS)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    RoleName.SYSTEM_ADMINISTRATOR: set(_ALL),
    RoleName.IT_MANAGER: {
        "intern.read", "intern.export",
        "shift.read", "schedule.read",
        "attendance.read", "attendance.approve", "overtime.approve",
        "ojt.read",
        "leave.read", "leave.approve",
        "timesheet.read", "timesheet.generate", "timesheet.sign",
        "exposure.read", "evaluation.read",
        "dashboard.read", "dashboard.compliance", "dutyboard.read",
        "report.read", "report.export",
        "audit.read",
    },
    RoleName.IT_SUPERVISOR: {
        "intern.read.assigned", "intern.manage",
        "shift.read",
        "schedule.read", "schedule.assign", "schedule.approve",
        "attendance.read.assigned", "attendance.manage", "attendance.approve",
        "overtime.approve",
        "ojt.read",
        "leave.read", "leave.approve",
        "timesheet.read", "timesheet.generate", "timesheet.sign",
        "exposure.read", "exposure.log", "exposure.verify",
        "evaluation.read", "evaluation.manage",
        "dashboard.read", "dutyboard.read",
        "report.read", "report.export",
    },
    RoleName.IT_MENTOR: {
        "intern.read.assigned",
        "schedule.read",
        "attendance.read.assigned",
        "ojt.read",
        "exposure.read", "exposure.log", "exposure.verify",
        "evaluation.read", "evaluation.manage",
        "dutyboard.read",
        "report.read",
    },
    RoleName.INTERN: {
        "intern.read.self",
        "schedule.read.self",
        "attendance.read.self", "attendance.punch",
        "attendance.request_correction",
        "ojt.read.self",
        "leave.request",
        "timesheet.read.self", "timesheet.submit",
        "exposure.read.self",
    },
}

ROLE_DISPLAY: dict[str, str] = {
    RoleName.SYSTEM_ADMINISTRATOR: "System Administrator",
    RoleName.IT_MANAGER: "IT Manager",
    RoleName.IT_SUPERVISOR: "IT Supervisor",
    RoleName.IT_MENTOR: "IT Mentor",
    RoleName.INTERN: "OJT / Intern",
}

# Roles that may act on behalf of, or approve for, an intern. Used by the
# self-approval guard: an intern is never in this set.
APPROVER_ROLES: set[str] = {
    RoleName.SYSTEM_ADMINISTRATOR,
    RoleName.IT_MANAGER,
    RoleName.IT_SUPERVISOR,
}


def validate_catalogue() -> None:
    """Fail loudly if a role references a permission that does not exist."""
    for role, perms in ROLE_PERMISSIONS.items():
        unknown = perms - _ALL
        if unknown:
            raise RuntimeError(
                f"Role {role} references unknown permissions: {sorted(unknown)}"
            )
