"""Attendance records, breaks and adjustments.

Design notes
------------
* ``business_date`` is the SCHEDULED START DATE of the matched shift. An
  overnight session timed in at 21:55 on Nov 20 and out at 07:05 on Nov 21 is
  ONE record whose business_date is Nov 20. Never two records.
* Every record carries a snapshot of the shift configuration that applied when
  it was created, so later edits to a ShiftTemplate cannot retroactively change
  historical computations.
* Computed minute columns are a cache for reporting. The source of truth for
  OJT totals is the append-only OJTHourLedger.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON,
    String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.core.enums import ApprovalStatus, AttendanceStatus
from app.core.timeutil import minutes_to_hours
from app.models.base import ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin


class AttendanceRecord(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, RemarksMixin):
    __tablename__ = "attendance_records"
    __table_args__ = (
        # One record per intern per business date per session sequence. The
        # sequence allows a deliberate second session on the same business date
        # (split duty) while still blocking accidental duplicates.
        UniqueConstraint(
            "intern_id", "business_date", "session_sequence", name="uq_attendance_intern_date_seq"
        ),
        CheckConstraint("session_sequence >= 1", name="ck_attendance_seq_positive"),
        Index("ix_attendance_date_status", "business_date", "status"),
        Index("ix_attendance_intern_date", "intern_id", "business_date"),
        Index("ix_attendance_open_sessions", "intern_id", "time_out"),
        Index("ix_attendance_approval", "approval_status", "business_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Business date binding ----------------------------------------------
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    session_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- Raw punches (stored UTC, aware) ------------------------------------
    time_in: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    time_out: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # --- Schedule snapshot (immutable once written) -------------------------
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id", ondelete="SET NULL"), nullable=True
    )
    shift_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    scheduled_end: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    is_overnight_shift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Full config snapshot - see ShiftTemplate.snapshot()
    shift_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Scheduled figures --------------------------------------------------
    scheduled_elapsed_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scheduled_break_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scheduled_net_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Actual figures -----------------------------------------------------
    actual_elapsed_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_break_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unpaid_break_deducted_minutes: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # Preserved separately from creditable minutes, per spec.
    actual_net_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Exceptions ---------------------------------------------------------
    late_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    undertime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_overtime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    overtime_approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # --- OJT crediting ------------------------------------------------------
    creditable_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credit_capped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cap_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- Status & approval --------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(28), default=AttendanceStatus.SCHEDULED, nullable=False, index=True
    )
    approval_status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.PENDING, nullable=False, index=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    supervisor_remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # --- Provenance ---------------------------------------------------------
    # DIGITAL_TIMEKEEPING | MANUAL_ENTRY | SYSTEM_GENERATED
    source: Mapped[str] = mapped_column(
        String(24), default="DIGITAL_TIMEKEEPING", nullable=False
    )
    time_in_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_out_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Relationships ------------------------------------------------------
    intern: Mapped["Intern"] = relationship(back_populates="attendance_records")
    breaks: Mapped[list["AttendanceBreak"]] = relationship(
        back_populates="attendance_record",
        cascade="all, delete-orphan",
        order_by="AttendanceBreak.break_start",
    )
    adjustments: Mapped[list["AttendanceAdjustment"]] = relationship(
        back_populates="attendance_record", cascade="all, delete-orphan"
    )

    # --- Convenience --------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """Timed in but not out."""
        return self.time_in is not None and self.time_out is None

    @property
    def has_complete_punches(self) -> bool:
        return self.time_in is not None and self.time_out is not None

    @property
    def is_approved(self) -> bool:
        return self.approval_status == ApprovalStatus.APPROVED

    @property
    def actual_net_hours(self) -> float:
        return minutes_to_hours(self.actual_net_minutes)

    @property
    def creditable_hours(self) -> float:
        return minutes_to_hours(self.creditable_minutes)

    @property
    def scheduled_net_hours(self) -> float:
        return minutes_to_hours(self.scheduled_net_minutes)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AttendanceRecord intern={self.intern_id} {self.business_date} "
            f"{self.shift_code} {self.status}>"
        )


class AttendanceBreak(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    """A break interval inside an attendance session. Multiple breaks allowed."""

    __tablename__ = "attendance_breaks"
    __table_args__ = (
        Index("ix_break_record", "attendance_record_id", "break_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_record_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    break_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    break_end: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # LUNCH | SHORT_BREAK | OTHER
    break_type: Mapped[str] = mapped_column(String(24), default="LUNCH", nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    attendance_record: Mapped[AttendanceRecord] = relationship(back_populates="breaks")

    @property
    def is_open(self) -> bool:
        return self.break_end is None


class AttendanceAdjustment(Base, TimestampMixin, SoftDeleteMixin):
    """Correction request / approved adjustment against an attendance record.

    Rows are never edited after approval - they are the audit trail for why a
    record's figures differ from the raw punches.
    """

    __tablename__ = "attendance_adjustments"
    __table_args__ = (
        Index("ix_adjustment_status", "approval_status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="CASCADE"), nullable=True, index=True
    )
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # TIME_IN | TIME_OUT | BREAK | HOURS_CREDIT | STATUS | OVERTIME
    adjustment_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    supporting_document_path: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Requested values (nullable per type)
    requested_time_in: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    requested_time_out: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    requested_status: Mapped[str | None] = mapped_column(String(28), nullable=True)
    # Signed minute delta for HOURS_CREDIT adjustments.
    requested_minutes_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Values before the change, captured for the audit trail.
    previous_time_in: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    previous_time_out: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(28), nullable=True)
    previous_creditable_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approval_status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.PENDING, nullable=False, index=True
    )
    # Enforced in the service layer: an intern can never be the approver of
    # their own correction.
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    decision_remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    attendance_record: Mapped[AttendanceRecord | None] = relationship(
        back_populates="adjustments"
    )
