"""Intern / OJT master record."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedString, EncryptedText, blind_index
from app.core.database import Base
from app.core.enums import InternStatus
from app.models.base import ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin


class Intern(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, RemarksMixin):
    __tablename__ = "interns"
    __table_args__ = (
        Index("ix_interns_status_dept", "status", "department_id"),
        Index("ix_interns_supervisor_status", "supervisor_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-facing identifier, generated as OJT-<YYYY>-<NNNN>.
    intern_code: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, index=True
    )

    # --- Personal -----------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # --- Encrypted at rest (Fernet) -----------------------------------------
    # Personal and emergency contact details are stored as ciphertext. They are
    # read and written as ordinary strings by application code; only the
    # database file, dumps and backups see the tokens.
    contact_number: Mapped[str | None] = mapped_column(
        EncryptedString(255), nullable=True
    )
    email: Mapped[str | None] = mapped_column(EncryptedString(400), nullable=True)
    # Keyed HMAC of the lowercase email, maintained by the service layer. An
    # encrypted column cannot be searched, so lookup-by-email uses this.
    email_index: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(
        EncryptedString(400), nullable=True
    )
    emergency_contact_number: Mapped[str | None] = mapped_column(
        EncryptedString(255), nullable=True
    )
    emergency_contact_relation: Mapped[str | None] = mapped_column(
        EncryptedString(255), nullable=True
    )
    profile_photo_path: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # --- Academic -----------------------------------------------------------
    school_id: Mapped[int | None] = mapped_column(
        ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True
    )
    course: Mapped[str | None] = mapped_column(String(180), nullable=True)
    year_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    school_coordinator: Mapped[str | None] = mapped_column(String(180), nullable=True)

    # --- OJT terms ----------------------------------------------------------
    ojt_start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expected_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Hard date the school requires the signed timesheet by. Drives the
    # completion forecast risk flags - distinct from expected_completion_date,
    # which is only a plan.
    school_submission_deadline: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    actual_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    required_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=486)

    # --- Assignment ---------------------------------------------------------
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supervisor_id: Mapped[int | None] = mapped_column(
        ForeignKey("supervisors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mentor_id: Mapped[int | None] = mapped_column(
        ForeignKey("mentors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    primary_technical_function: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default=InternStatus.PENDING, nullable=False, index=True
    )

    # --- Login link ---------------------------------------------------------
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )

    # --- Per-intern computation overrides -----------------------------------
    # NULL means "inherit the system setting". Lets one intern be capped at a
    # different creditable-hours-per-day without changing global policy.
    max_creditable_minutes_per_day: Mapped[int | None] = mapped_column(nullable=True)
    counts_approved_leave_as_hours: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    # --- Relationships ------------------------------------------------------
    school: Mapped["School | None"] = relationship(back_populates="interns")
    department: Mapped["Department | None"] = relationship(back_populates="interns")
    team: Mapped["Team | None"] = relationship(back_populates="interns")
    supervisor: Mapped["Supervisor | None"] = relationship(
        back_populates="interns", foreign_keys=[supervisor_id]
    )
    mentor: Mapped["Mentor | None"] = relationship(
        back_populates="interns", foreign_keys=[mentor_id]
    )
    user: Mapped["User | None"] = relationship(
        back_populates="intern_profile", foreign_keys=[user_id]
    )

    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="intern", cascade="all, delete-orphan"
    )
    schedule_assignments: Mapped[list["ScheduleAssignment"]] = relationship(
        back_populates="intern", cascade="all, delete-orphan"
    )
    ledger_entries: Mapped[list["OJTHourLedger"]] = relationship(
        back_populates="intern", cascade="all, delete-orphan"
    )
    leave_requests: Mapped[list["LeaveRequest"]] = relationship(
        back_populates="intern", cascade="all, delete-orphan"
    )

    # --- Convenience --------------------------------------------------------
    def sync_lookup_indexes(self) -> None:
        """Recompute blind indexes. Call after changing an encrypted field."""
        self.email_index = blind_index(self.email)

    @property
    def full_name(self) -> str:
        middle = f" {self.middle_name}" if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        return f"{self.last_name}, {self.first_name}"

    @property
    def required_minutes(self) -> int:
        return int(self.required_hours * 60)

    @property
    def is_countable(self) -> bool:
        """Included in dashboard 'active' counts."""
        return self.status in (InternStatus.ACTIVE, InternStatus.ON_LEAVE)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Intern {self.intern_code} {self.display_name}>"
