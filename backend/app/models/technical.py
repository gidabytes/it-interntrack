"""Technical assignments, exposure tracking and intern evaluations."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.core.enums import ProficiencyLevel
from app.models.base import ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin


class TechnicalAssignment(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, RemarksMixin):
    """Which IT function an intern is assigned to, and for how long."""

    __tablename__ = "technical_assignments"
    __table_args__ = (
        Index("ix_techassign_intern_range", "intern_id", "date_from", "date_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # See enums.TechnicalFunction
    technical_function: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    objectives: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    intern: Mapped["Intern"] = relationship()


class ExposureLog(Base, TimestampMixin, SoftDeleteMixin, ActorMixin):
    """Technical Exposure Tracker - one row per logged hands-on activity.

    This is what turns "the intern was present for 486 hours" into "the intern
    actually did AD administration for 24 hours and patching for 16". Hours
    here are experience hours and are deliberately INDEPENDENT of the OJT hour
    ledger - logging exposure never credits attendance.
    """

    __tablename__ = "exposure_logs"
    __table_args__ = (
        CheckConstraint("minutes_spent >= 0", name="ck_exposure_minutes_nonneg"),
        Index("ix_exposure_intern_area", "intern_id", "exposure_area"),
        Index("ix_exposure_date", "activity_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # See enums.ExposureArea
    exposure_area: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    activity_title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    minutes_spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    proficiency_level: Mapped[str] = mapped_column(
        String(28), default=ProficiencyLevel.ASSISTED, nullable=False
    )
    # Optional linkage to the ticketing / project reference used on the floor.
    reference_ticket: Mapped[str | None] = mapped_column(String(80), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    asset_tag: Mapped[str | None] = mapped_column(String(80), nullable=True)

    logged_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    mentor_feedback: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    intern: Mapped["Intern"] = relationship()

    @property
    def is_verified(self) -> bool:
        return self.verified_by_id is not None


class ExposureTarget(Base, TimestampMixin, SoftDeleteMixin):
    """Expected minimum exposure per area, used to compute coverage gaps.

    Scoped either globally (intern_id NULL, team_id NULL), per team, or per
    intern; the most specific target wins.
    """

    __tablename__ = "exposure_targets"
    __table_args__ = (
        UniqueConstraint(
            "exposure_area", "intern_id", "team_id", name="uq_exposure_target_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exposure_area: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    intern_id: Mapped[int | None] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InternEvaluation(Base, TimestampMixin, SoftDeleteMixin, ActorMixin):
    """Weekly / periodic performance evaluation by a supervisor or mentor."""

    __tablename__ = "intern_evaluations"
    __table_args__ = (
        Index("ix_eval_intern_period", "intern_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intern_id: Mapped[int] = mapped_column(
        ForeignKey("interns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # WEEKLY | MONTHLY | FINAL
    evaluation_type: Mapped[str] = mapped_column(String(20), default="WEEKLY", nullable=False)

    # 1-5 scores
    technical_skill_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attendance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attitude_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initiative_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    communication_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    skills_acquired: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    tasks_assigned: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    projects_participated: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    accomplishments: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    development_areas: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    mentor_feedback: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    evaluator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # EvaluatorRole: IT_SUPERVISOR | IT_MENTOR
    evaluator_role: Mapped[str | None] = mapped_column(String(30), nullable=True)

    intern: Mapped["Intern"] = relationship()

    @property
    def overall_score(self) -> float | None:
        scores = [
            s for s in (
                self.technical_skill_score, self.attendance_score, self.attitude_score,
                self.initiative_score, self.communication_score,
            ) if s is not None
        ]
        return round(sum(scores) / len(scores), 2) if scores else None
