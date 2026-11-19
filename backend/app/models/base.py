"""Shared column mixins."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import UTCDateTime
from app.core.timeutil import utc_now


class TimestampMixin:
    """created_at / updated_at on every table."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Archive instead of destroy, so history stays traceable."""

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deleted_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ActorMixin:
    """Who created / last changed the row."""

    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RemarksMixin:
    remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)
