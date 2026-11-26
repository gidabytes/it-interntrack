"""Intern self-registration.

Applicants submit their own details through a public link rather than being
keyed in by hand, which is where most of the transcription errors in school
paperwork come from. A submission is inert until a supervisor approves it: it
creates no intern, no user account and no schedule, so an open registration
link cannot be used to inject records into the live system.

Encryption follows the same rule as the intern record: the applicant's own
contact details are ciphertext at rest, while the school's OJT coordinator
details are not. That asymmetry is deliberate. A coordinator address is an
institutional contact that has to be searchable and is printed on the signed
timesheet that goes back to the school, whereas the applicant's personal
number and emergency contact have no reason to be readable in a backup.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedString, blind_index
from app.core.database import Base, UTCDateTime
from app.models.base import TimestampMixin


class RegistrationStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"

    ALL = (PENDING, APPROVED, REJECTED, WITHDRAWN)


class InternRegistration(Base, TimestampMixin):
    __tablename__ = "intern_registrations"
    __table_args__ = (
        Index("ix_registration_status_created", "status", "created_at"),
        # Supports the per-address and per-IP throttles without a table scan.
        Index("ix_registration_submitted_ip", "submitted_ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Given to the applicant so they can check their own status without an
    # account. Random rather than sequential: a sequential code would let
    # anyone enumerate every applicant's submission.
    reference_code: Mapped[str] = mapped_column(
        String(24), unique=True, nullable=False, index=True
    )

    # --- Applicant ----------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    email: Mapped[str] = mapped_column(EncryptedString(400), nullable=False)
    # Keyed HMAC of the lowercase address. An encrypted column cannot be
    # searched, so the duplicate check uses this.
    email_index: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    contact_number: Mapped[str | None] = mapped_column(
        EncryptedString(255), nullable=True
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

    # --- School -------------------------------------------------------------
    # Either an existing school is chosen, or a name is typed for one not yet
    # on file; the reviewer resolves the second case on approval. Storing the
    # typed name rather than creating the school on submission keeps an open
    # link from filling the schools list with junk.
    school_id: Mapped[int | None] = mapped_column(
        ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True
    )
    school_name_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    course: Mapped[str | None] = mapped_column(String(180), nullable=True)
    year_level: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- School's OJT / IT coordinator -------------------------------------
    # Who at the school signs off the hours and receives the timesheet.
    coordinator_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    coordinator_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    coordinator_contact: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- OJT terms ----------------------------------------------------------
    required_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ojt_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    school_submission_deadline: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    primary_technical_function: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    applicant_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Review -------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(20), default=RegistrationStatus.PENDING, nullable=False, index=True
    )
    submitted_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_user_agent: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set once approved, so the registration remains an audit record of what
    # the applicant actually typed rather than being edited into the intern.
    created_intern_id: Mapped[int | None] = mapped_column(
        ForeignKey("interns.id", ondelete="SET NULL"), nullable=True
    )

    school = relationship("School", foreign_keys=[school_id])

    @property
    def applicant_name(self) -> str:
        middle = f" {self.middle_name}" if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}"

    @property
    def school_label(self) -> str | None:
        """Whichever school name is known, preferring the one on file."""
        if self.school is not None:
            return self.school.name
        return self.school_name_text

    def refresh_email_index(self) -> None:
        self.email_index = blind_index(self.email)
