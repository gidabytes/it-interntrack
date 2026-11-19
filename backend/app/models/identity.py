"""Users, roles and permissions (RBAC)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Table, Column, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedString, blind_index
from app.core.database import Base, UTCDateTime
from app.models.base import ActorMixin, SoftDeleteMixin, TimestampMixin

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")

    roles: Mapped[list["Role"]] = relationship(
        secondary=role_permissions, back_populates="permissions"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Permission {self.code}>"


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(255), default="")

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )
    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role {self.name}>"


class User(Base, TimestampMixin, SoftDeleteMixin, ActorMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_username_active", "username", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    # Encrypted at rest. Fernet is non-deterministic, so a UNIQUE constraint on
    # the ciphertext would not catch duplicates and a WHERE email = ? would
    # never match - uniqueness and lookup both ride on email_index instead.
    email: Mapped[str] = mapped_column(EncryptedString(400), nullable=False)
    email_index: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # bcrypt digest - a password hash, not encryption, and never reversible.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(180), nullable=False)
    employee_id: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    contact_number: Mapped[str | None] = mapped_column(
        EncryptedString(255), nullable=True
    )
    position_title: Mapped[str | None] = mapped_column(String(120), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )
    department: Mapped["Department | None"] = relationship(back_populates="users")
    intern_profile: Mapped["Intern | None"] = relationship(
        back_populates="user", uselist=False, foreign_keys="Intern.user_id"
    )

    # --- convenience --------------------------------------------------------
    def sync_lookup_indexes(self) -> None:
        """Recompute blind indexes. Call after changing the email."""
        self.email_index = blind_index(self.email) or ""

    @property
    def role_names(self) -> set[str]:
        return {r.name for r in self.roles}

    @property
    def permission_codes(self) -> set[str]:
        codes: set[str] = set()
        for role in self.roles:
            codes.update(p.code for p in role.permissions)
        return codes

    def has_role(self, *names: str) -> bool:
        return bool(self.role_names.intersection(names))

    def has_permission(self, code: str) -> bool:
        return code in self.permission_codes

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"
