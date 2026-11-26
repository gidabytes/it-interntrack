"""Schools and their OJT / IT coordinators."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel


class SchoolBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    short_name: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=300)
    # The school-side contact who signs off OJT hours and receives the
    # completed timesheet. Commonly titled OJT Coordinator or, for IT
    # programmes, the IT Coordinator.
    coordinator_name: str | None = Field(default=None, max_length=180)
    coordinator_email: EmailStr | None = None
    coordinator_contact: str | None = Field(default=None, max_length=50)
    default_required_hours: int | None = Field(default=None, ge=1, le=10000)
    remarks: str | None = None

    @field_validator("name", "short_name", "coordinator_name", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("short_name", "address", "coordinator_name",
                     "coordinator_contact", mode="after")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        # An empty field posted by a form means "not set", not "set to empty".
        return v or None


class SchoolCreate(SchoolBase):
    pass


class SchoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    short_name: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=300)
    coordinator_name: str | None = Field(default=None, max_length=180)
    coordinator_email: EmailStr | None = None
    coordinator_contact: str | None = Field(default=None, max_length=50)
    default_required_hours: int | None = Field(default=None, ge=1, le=10000)
    is_active: bool | None = None
    remarks: str | None = None


class SchoolOut(ORMModel):
    id: int
    name: str
    short_name: str | None
    address: str | None
    coordinator_name: str | None
    coordinator_email: str | None
    coordinator_contact: str | None
    default_required_hours: int | None
    is_active: bool
    remarks: str | None
    # Populated by the router so the list can show where the interns are
    # without a request per row.
    intern_count: int = 0
    active_intern_count: int = 0
