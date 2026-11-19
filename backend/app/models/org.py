"""Organisational structure: schools, departments, teams, supervisors, mentors."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import ActorMixin, RemarksMixin, SoftDeleteMixin, TimestampMixin


class School(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    short_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    coordinator_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    coordinator_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    coordinator_contact: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # School's own OJT requirement, used as the default for new interns.
    default_required_hours: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    interns: Mapped[list["Intern"]] = relationship(back_populates="school")


class Department(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    site_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    teams: Mapped[list["Team"]] = relationship(back_populates="department")
    users: Mapped[list["User"]] = relationship(back_populates="department")
    interns: Mapped[list["Intern"]] = relationship(back_populates="department")


class Team(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("department_id", "code", name="uq_team_dept_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Primary technical function of the team (see enums.TechnicalFunction).
    technical_function: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    department: Mapped[Department] = relationship(back_populates="teams")
    interns: Mapped[list["Intern"]] = relationship(back_populates="team")


class Supervisor(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    """An IT Supervisor record, linked to the login account that acts as them."""

    __tablename__ = "supervisors"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    # Cap used by the compliance dashboard to flag overloaded supervisors.
    max_interns: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    interns: Mapped[list["Intern"]] = relationship(
        back_populates="supervisor", foreign_keys="Intern.supervisor_id"
    )


class Mentor(Base, TimestampMixin, SoftDeleteMixin, RemarksMixin):
    """An IT Mentor record, linked to the login account that acts as them."""

    __tablename__ = "mentors"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    specialization: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    interns: Mapped[list["Intern"]] = relationship(
        back_populates="mentor", foreign_keys="Intern.mentor_id"
    )
