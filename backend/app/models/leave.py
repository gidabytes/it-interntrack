"""Leave and absence requests."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.core.enums import ApprovalStatus, LeaveType
from app.models.base import SoftDeleteMixin, TimestampMixin


class LeaveRequest(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("date_to >= date_from", name="ck_leave_date_order"),
        Index("ix_leave_intern_range", "intern_id", "date_from", "date_to"),
        Index("ix_leave_status", "approval_status", "date_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    leave_type: Mapped[str] = mapped_column(
        String(24), default=LeaveType.PERSONAL, nullable=False
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_half_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    supporting_document_path: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
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
    decision_remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Approved leave does NOT credit OJT hours unless an administrator sets
    # this explicitly (per-request) or enables the global policy setting.
    is_creditable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credited_minutes_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the leave is Official Business, which maps attendance status to
    # OFFICIAL_BUSINESS rather than ON_LEAVE.
    is_official_business: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    intern: Mapped["Intern"] = relationship(back_populates="leave_requests")

    @property
    def is_approved(self) -> bool:
        return self.approval_status == ApprovalStatus.APPROVED

    def covers_date(self, d: date) -> bool:
        return (not self.is_deleted) and self.date_from <= d <= self.date_to
