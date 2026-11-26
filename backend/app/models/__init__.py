"""Model registry.

Importing this package registers every table on ``Base.metadata``. The order
matters only for readability; SQLAlchemy resolves relationships lazily by name.
"""
from app.models.base import (  # noqa: F401
    ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin,
)
from app.models.identity import (  # noqa: F401
    Permission, Role, User, role_permissions, user_roles,
)
from app.models.org import Department, Mentor, School, Supervisor, Team  # noqa: F401
from app.models.intern import Intern  # noqa: F401
from app.models.schedule import (  # noqa: F401
    Holiday, RestDay, ScheduleAssignment, ShiftTemplate,
)
from app.models.attendance import (  # noqa: F401
    AttendanceAdjustment, AttendanceBreak, AttendanceRecord,
)
from app.models.ojt import OJTHourLedger, OJTProgressSnapshot  # noqa: F401
from app.models.leave import LeaveRequest  # noqa: F401
from app.models.technical import (  # noqa: F401
    ExposureLog, ExposureTarget, InternEvaluation, TechnicalAssignment,
)
from app.models.timesheet import Timesheet  # noqa: F401
from app.models.system import AuditLog, Notification, SystemSetting  # noqa: F401
from app.models.registration import (  # noqa: F401
    InternRegistration, RegistrationStatus,
)

__all__ = [
    "ActorMixin", "RemarksMixin", "SoftDeleteMixin", "TimestampMixin",
    "Permission", "Role", "User", "role_permissions", "user_roles",
    "Department", "Mentor", "School", "Supervisor", "Team",
    "Intern",
    "Holiday", "RestDay", "ScheduleAssignment", "ShiftTemplate",
    "AttendanceAdjustment", "AttendanceBreak", "AttendanceRecord",
    "OJTHourLedger", "OJTProgressSnapshot",
    "LeaveRequest",
    "ExposureLog", "ExposureTarget", "InternEvaluation", "TechnicalAssignment",
    "Timesheet",
    "AuditLog", "Notification", "SystemSetting",
    "InternRegistration", "RegistrationStatus",
]
