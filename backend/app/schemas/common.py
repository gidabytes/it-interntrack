"""Shared schema pieces."""
from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class DateRangeFilter(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    preset: str | None = Field(
        default=None,
        description="TODAY | THIS_WEEK | THIS_MONTH | THIS_QUARTER | CUSTOM",
    )
