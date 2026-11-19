"""Append-only OJT hour ledger.

The ledger is the single source of truth for rendered / approved OJT hours.
Rows are NEVER updated once approved: a correction posts a REVERSAL row plus a
replacement row, so the history of every computation stays intact and auditable.
Totals are always derived with SUM over approved, non-reversed rows.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.core.enums import LedgerSourceType
from app.core.timeutil import minutes_to_hours
from app.models.base import TimestampMixin


class OJTHourLedger(Base, TimestampMixin):
    __tablename__ = "ojt_hour_ledger"
    __table_args__ = (
        # Lookup path for "is this source already posted and live?". NOT
        # unique: the reverse-and-repost pattern deliberately leaves an old
        # reversed row for the same source alongside its replacement. The
        # "only one live posting per source" rule is enforced in
        # services.ojt_engine, which checks for a non-reversed row first.
        Index("ix_ledger_source", "intern_id", "source_type", "source_id", "is_reversed"),
        Index("ix_ledger_intern_approved", "intern_id", "is_approved", "is_reversed"),
        Index("ix_ledger_business_date", "intern_id", "business_date"),
        CheckConstraint(
            "minutes_delta <> 0 OR source_type = 'OPENING_BALANCE'",
            name="ck_ledger_nonzero",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Where the hours came from.
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Signed, so reversals and deductions are first-class entries.
    minutes_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    # Actual rendered minutes behind this entry, preserved even when the
    # creditable delta was capped. Never used for OJT totals.
    actual_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Only approved, non-reversed rows count toward OJT hours.
    is_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )

    is_reversed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("ojt_hour_ledger.id", ondelete="SET NULL"), nullable=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    reversed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    description: Mapped[str] = mapped_column(String(300), default="")
    # Snapshot of the computation rules in force when this entry was posted.
    computation_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    intern: Mapped["Intern"] = relationship(back_populates="ledger_entries")

    @property
    def hours_delta(self) -> float:
        return minutes_to_hours(self.minutes_delta)

    @property
    def counts_toward_total(self) -> bool:
        return self.is_approved and not self.is_reversed

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OJTHourLedger intern={self.intern_id} {self.business_date} "
            f"{self.minutes_delta:+d}m approved={self.is_approved}>"
        )


class OJTProgressSnapshot(Base, TimestampMixin):
    """Historical calculation record.

    Written whenever progress is recomputed, so the system can show how a
    completion figure looked at a past point in time without recomputing from
    a mutated ledger.
    """

    __tablename__ = "ojt_progress_snapshots"
    __table_args__ = (
        Index("ix_snapshot_intern_date", "intern_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    required_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_rendered_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remaining_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_percentage: Mapped[float] = mapped_column(nullable=False, default=0.0)

    projected_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(24), nullable=True)
    avg_minutes_per_week: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
