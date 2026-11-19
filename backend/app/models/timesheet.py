"""Digital timesheets, reviewed and signed by the IT Supervisor.

A timesheet is a point-in-time, immutable rendering of the attendance figures
for a period. Once SIGNED the stored line items are frozen - later attendance
corrections produce a NEW revision rather than silently changing a document the
supervisor already put their name to.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.core.enums import TimesheetPeriod, TimesheetStatus
from app.core.timeutil import minutes_to_hours
from app.models.base import SoftDeleteMixin, TimestampMixin


class Timesheet(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "timesheets"
    __table_args__ = (
        UniqueConstraint(
            "intern_id", "period_type", "period_start", "revision",
            name="uq_timesheet_intern_period_rev",
        ),
        Index("ix_timesheet_status", "status", "period_start"),
        Index("ix_timesheet_intern", "intern_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Human reference printed on the document, e.g. TS-2026-11-OJT-2026-0004-R1
    reference_no: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    period_type: Mapped[str] = mapped_column(
        String(10), default=TimesheetPeriod.MONTHLY, nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- Frozen totals ------------------------------------------------------
    total_days_scheduled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_days_present: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_days_absent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_days_late: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_scheduled_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_actual_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_creditable_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_late_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_undertime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_overtime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Running OJT position as of period_end, frozen at generation.
    cumulative_approved_minutes: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    required_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remaining_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Frozen day-by-day line items (list of dicts; see services.timesheet).
    line_items: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- Review / signature -------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16), default=TimesheetStatus.DRAFT, nullable=False, index=True
    )
    generated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    submitted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    signed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    signed_by_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    signed_by_position: Mapped[str | None] = mapped_column(String(120), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    # SHA-256 over the frozen totals + line items + signer, so tampering with
    # a signed timesheet is detectable.
    signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Set when a newer revision supersedes this one.
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("timesheets.id", ondelete="SET NULL"), nullable=True
    )

    intern: Mapped["Intern"] = relationship()

    @property
    def is_signed(self) -> bool:
        return self.status == TimesheetStatus.SIGNED and self.signed_at is not None

    @property
    def total_creditable_hours(self) -> float:
        return minutes_to_hours(self.total_creditable_minutes)

    @property
    def total_actual_hours(self) -> float:
        return minutes_to_hours(self.total_actual_minutes)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Timesheet {self.reference_no} {self.status}>"
