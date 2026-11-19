from __future__ import annotations

from datetime import date

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.enums import InternStatus
from app.schemas.common import ORMModel


class InternBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    contact_number: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=180)
    emergency_contact_number: str | None = Field(default=None, max_length=50)
    emergency_contact_relation: str | None = Field(default=None, max_length=80)

    school_id: int | None = None
    course: str | None = Field(default=None, max_length=180)
    year_level: str | None = Field(default=None, max_length=40)

    ojt_start_date: date
    expected_completion_date: date | None = None
    school_submission_deadline: date | None = None
    required_hours: int = Field(default=486, ge=1, le=5000)

    department_id: int | None = None
    team_id: int | None = None
    supervisor_id: int | None = None
    mentor_id: int | None = None
    primary_technical_function: str | None = None
    remarks: str | None = Field(default=None, max_length=1000)

    @field_validator("expected_completion_date", "school_submission_deadline")
    @classmethod
    def _not_before_start(cls, v, info):
        start = info.data.get("ojt_start_date")
        if v and start and v < start:
            raise ValueError(
                f"{info.field_name} ({v}) cannot be before the OJT start date ({start})."
            )
        return v


class InternCreate(InternBase):
    status: str = InternStatus.PENDING


class InternUpdate(BaseModel):
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    contact_number: str | None = None
    email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_number: str | None = None
    emergency_contact_relation: str | None = None
    school_id: int | None = None
    course: str | None = None
    year_level: str | None = None
    expected_completion_date: date | None = None
    school_submission_deadline: date | None = None
    required_hours: int | None = Field(default=None, ge=1, le=5000)
    department_id: int | None = None
    team_id: int | None = None
    supervisor_id: int | None = None
    mentor_id: int | None = None
    primary_technical_function: str | None = None
    status: str | None = None
    remarks: str | None = None
    max_creditable_minutes_per_day: int | None = Field(default=None, ge=0, le=1440)


class InternOut(ORMModel):
    id: int
    intern_code: str
    first_name: str
    middle_name: str | None
    last_name: str
    full_name: str
    display_name: str
    contact_number: str | None
    email: str | None
    emergency_contact_name: str | None
    emergency_contact_number: str | None
    emergency_contact_relation: str | None
    school_id: int | None
    course: str | None
    year_level: str | None
    ojt_start_date: date
    expected_completion_date: date | None
    school_submission_deadline: date | None
    actual_completion_date: date | None
    required_hours: int
    department_id: int | None
    team_id: int | None
    supervisor_id: int | None
    mentor_id: int | None
    primary_technical_function: str | None
    status: str
    user_id: int | None
    remarks: str | None


class InternSummaryOut(ORMModel):
    id: int
    intern_code: str
    display_name: str
    team_id: int | None
    status: str
    required_hours: int
    school_submission_deadline: date | None
