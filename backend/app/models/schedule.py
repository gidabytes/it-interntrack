"""Shift templates, schedule assignments, holidays and rest days."""
from __future__ import annotations

from datetime import date, time

from sqlalchemy import (
    Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import ApprovalStatus, AssignmentType, ASSIGNMENT_PRECEDENCE
from app.core.timeutil import is_overnight, shift_elapsed_minutes
from app.models.base import ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin


class ShiftTemplate(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, RemarksMixin):
    """A reusable shift definition, e.g. SHIFT-16 = 22:00-07:00 overnight.

    Templates are versioned by effective dates and NEVER edited in a way that
    changes past computations: every attendance session stores its own snapshot
    of the template values that applied at the time (see AttendanceRecord).
    """

    __tablename__ = "shift_templates"
    __table_args__ = (
        CheckConstraint("break_minutes >= 0", name="ck_shift_break_nonneg"),
        CheckConstraint("grace_period_minutes >= 0", name="ck_shift_grace_nonneg"),
        Index("ix_shift_active_effective", "is_active", "effective_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    # Break policy
    break_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    break_is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tolerances
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    # Minutes after the shift END before a no-show is finalised as ABSENT.
    absence_grace_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_overtime_minutes: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    # Minimum hours of rest required before the next shift starts.
    min_rest_hours: Mapped[int] = mapped_column(Integer, default=8, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_system_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    assignments: Mapped[list["ScheduleAssignment"]] = relationship(
        back_populates="shift_template"
    )

    # --- Derived values -----------------------------------------------------
    @property
    def is_overnight(self) -> bool:
        """True when the shift crosses midnight. Derived, never stored stale."""
        return is_overnight(self.start_time, self.end_time)

    @property
    def elapsed_minutes(self) -> int:
        """Nominal elapsed duration, e.g. 540 for every standard 9h shift."""
        return shift_elapsed_minutes(self.start_time, self.end_time)

    @property
    def unpaid_break_minutes(self) -> int:
        return 0 if self.break_is_paid else self.break_minutes

    @property
    def net_working_minutes(self) -> int:
        """Expected working minutes: elapsed minus unpaid break.

        07:00-16:00 with a 1h unpaid lunch is 480, not 540. The engine never
        credits the full elapsed duration by default.
        """
        return self.elapsed_minutes - self.unpaid_break_minutes

    def is_effective_on(self, d: date) -> bool:
        if self.effective_from and d < self.effective_from:
            return False
        if self.effective_to and d > self.effective_to:
            return False
        return True

    def snapshot(self) -> dict:
        """Immutable copy of the values the attendance engine must preserve."""
        return {
            "shift_code": self.code,
            "shift_name": self.name,
            "sched_start_time": self.start_time.strftime("%H:%M"),
            "sched_end_time": self.end_time.strftime("%H:%M"),
            "is_overnight": self.is_overnight,
            "elapsed_minutes": self.elapsed_minutes,
            "break_minutes": self.break_minutes,
            "break_is_paid": self.break_is_paid,
            "unpaid_break_minutes": self.unpaid_break_minutes,
            "net_working_minutes": self.net_working_minutes,
            "grace_period_minutes": self.grace_period_minutes,
            "absence_grace_minutes": self.absence_grace_minutes,
            "max_overtime_minutes": self.max_overtime_minutes,
            "min_rest_hours": self.min_rest_hours,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShiftTemplate {self.code} {self.start_time}-{self.end_time}>"


class ScheduleAssignment(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, RemarksMixin):
    """Binds an intern to a shift template on particular days.

    One table covers every assignment method in the spec; ``assignment_type``
    plus the precedence map decides which row wins for a given date:

      DAILY_OVERRIDE  > TEMPORARY > ROTATING > WEEKLY_RECURRING

    * WEEKLY_RECURRING - one row per weekday, open-ended or date-bounded.
    * ROTATING         - adds rotation_week_index against a cycle anchor.
    * TEMPORARY        - a bounded date range replacing the normal pattern.
    * DAILY_OVERRIDE   - a single specific date.

    A row with ``is_rest_day`` true and no shift template marks a rest day,
    which is how rest days are configured independently per intern.
    """

    __tablename__ = "schedule_assignments"
    __table_args__ = (
        CheckConstraint(
            "(is_rest_day = 1 AND shift_template_id IS NULL) OR shift_template_id IS NOT NULL",
            name="ck_assignment_shift_or_rest",
        ),
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_assignment_dow_range",
        ),
        Index("ix_assign_intern_type", "intern_id", "assignment_type", "is_deleted"),
        Index("ix_assign_specific_date", "intern_id", "specific_date"),
        Index("ix_assign_effective", "intern_id", "effective_from", "effective_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    assignment_type: Mapped[str] = mapped_column(
        String(24), default=AssignmentType.WEEKLY_RECURRING, nullable=False
    )
    # 0 = Monday ... 6 = Sunday. NULL on DAILY_OVERRIDE (date implies the day)
    # and on TEMPORARY rows meant to cover every day in the range.
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Rotating-schedule support
    rotation_cycle_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation_week_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation_anchor_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_rest_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    # Schedule modifications require supervisor approval.
    approval_status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.APPROVED, nullable=False, index=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped["date | None"] = mapped_column(Date, nullable=True)
    # Set when a supervisor knowingly saved an assignment that failed a
    # conflict check (insufficient rest, overlap).
    conflict_override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    intern: Mapped["Intern"] = relationship(back_populates="schedule_assignments")
    shift_template: Mapped[ShiftTemplate | None] = relationship(back_populates="assignments")

    @property
    def precedence(self) -> int:
        return ASSIGNMENT_PRECEDENCE.get(self.assignment_type, 0)

    @property
    def is_approved(self) -> bool:
        return self.approval_status == ApprovalStatus.APPROVED

    def covers_date(self, d: date) -> bool:
        """Does this assignment apply to calendar date ``d``?"""
        if self.is_deleted or not self.is_approved:
            return False
        if d < self.effective_from:
            return False
        if self.effective_to and d > self.effective_to:
            return False

        if self.assignment_type == AssignmentType.DAILY_OVERRIDE:
            return self.specific_date == d

        if self.assignment_type == AssignmentType.ROTATING:
            if self.day_of_week is not None and self.day_of_week != d.weekday():
                return False
            anchor = self.rotation_anchor_date or self.effective_from
            cycle = self.rotation_cycle_weeks or 1
            if cycle <= 0:
                return False
            # Align both dates to their Monday so partial anchor weeks do not
            # shift the cycle.
            from app.core.timeutil import week_start

            weeks = (week_start(d) - week_start(anchor)).days // 7
            return (weeks % cycle) == (self.rotation_week_index or 0)

        # WEEKLY_RECURRING and TEMPORARY
        if self.day_of_week is None:
            return True  # covers every day in the range
        return self.day_of_week == d.weekday()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ScheduleAssignment intern={self.intern_id} {self.assignment_type} "
            f"dow={self.day_of_week} rest={self.is_rest_day}>"
        )


class Holiday(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    """Company / national holidays. Scope to a department for site-only days."""

    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("holiday_date", "department_id", name="uq_holiday_date_dept"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    # REGULAR | SPECIAL_NON_WORKING | LOCAL
    holiday_type: Mapped[str] = mapped_column(String(30), default="REGULAR", nullable=False)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # When false, a scheduled intern is still expected to report (24/7 ops).
    is_non_working: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_creditable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RestDay(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    """Explicit per-intern rest-day pattern.

    Rest days are also expressible as ScheduleAssignment rows; this table is
    the simpler weekly declaration used by the bulk assignment screens, and the
    resolver consults both.
    """

    __tablename__ = "rest_days"
    __table_args__ = (
        UniqueConstraint(
            "intern_id", "day_of_week", "effective_from", name="uq_restday_intern_dow_from"
        ),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_restday_dow"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    def covers_date(self, d: date) -> bool:
        if self.is_deleted or self.day_of_week != d.weekday():
            return False
        if d < self.effective_from:
            return False
        if self.effective_to and d > self.effective_to:
            return False
        return True
