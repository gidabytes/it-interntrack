"""Notifications, audit log and system settings."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.models.base import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_unread", "user_id", "is_read", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # See enums.AlertType
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), default="")
    # INFO | WARNING | CRITICAL
    severity: Mapped[str] = mapped_column(String(12), default="INFO", nullable=False)

    # Deep-link target, e.g. ("intern", 14) or ("attendance_record", 902).
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_url: Mapped[str | None] = mapped_column(String(300), nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Prevents re-sending the same alert every poll cycle.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)


class AuditLog(Base):
    """Immutable audit trail. Insert-only; never updated or deleted."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_actor", "actor_user_id", "created_at"),
        Index("ix_audit_action", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor_roles: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # CREATE | UPDATE | DELETE | APPROVE | REJECT | LOGIN | TIME_IN | ...
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(500), default="")

    before_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SystemSetting(Base, TimestampMixin):
    """Typed key/value configuration.

    Every computation rule the spec requires to be configurable lives here, so
    policy changes do not need a code deploy. See services.settings for the
    defaults and typed accessors.
    """

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    # int | float | bool | str | json
    value_type: Mapped[str] = mapped_column(String(10), default="str", nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="GENERAL", nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    is_editable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
