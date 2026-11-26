"""Intern self-registration: public submission and reviewer-facing views."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.common import ORMModel


class RegistrationCreate(BaseModel):
    """What the public form posts. Deliberately narrow.

    It carries no status, no intern code and no role: an applicant cannot set
    any of those, so an open endpoint cannot be used to create a privileged
    record. Everything here is a claim to be checked by a reviewer.
    """

    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    contact_number: str | None = Field(default=None, max_length=50)

    # One of the two must be given; the reviewer resolves a typed name.
    school_id: int | None = None
    school_name_text: str | None = Field(default=None, max_length=200)
    course: str | None = Field(default=None, max_length=180)
    year_level: str | None = Field(default=None, max_length=50)

    coordinator_name: str | None = Field(default=None, max_length=180)
    coordinator_email: EmailStr | None = None
    coordinator_contact: str | None = Field(default=None, max_length=50)

    required_hours: int | None = Field(default=None, ge=1, le=10000)
    ojt_start_date: date | None = None
    school_submission_deadline: date | None = None
    primary_technical_function: str | None = Field(default=None, max_length=80)

    emergency_contact_name: str | None = Field(default=None, max_length=180)
    emergency_contact_number: str | None = Field(default=None, max_length=50)
    emergency_contact_relation: str | None = Field(default=None, max_length=50)

    applicant_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @model_validator(mode="after")
    def _require_a_school(self):
        if self.school_id is None and not self.school_name_text:
            raise ValueError(
                "Choose your school from the list, or type its name if it is "
                "not there."
            )
        return self

    @model_validator(mode="after")
    def _deadline_after_start(self):
        if (
            self.ojt_start_date
            and self.school_submission_deadline
            and self.school_submission_deadline < self.ojt_start_date
        ):
            raise ValueError(
                "The school submission deadline cannot fall before the OJT "
                "start date."
            )
        return self


class RegistrationAck(BaseModel):
    """Returned to the applicant. Carries no personal data back.

    Echoing the submitted details would make the endpoint a way to confirm
    whether a given address is already on file, so only the reference code
    comes back.
    """

    reference_code: str
    status: str
    message: str


class RegistrationStatusOut(BaseModel):
    """Public status check by reference code."""

    reference_code: str
    status: str
    submitted_on: date
    reviewed_on: date | None = None
    # Shown to the applicant on rejection so they know what to fix; the
    # reviewer is told this is applicant-visible before they write it.
    review_notes: str | None = None


class RegistrationOut(ORMModel):
    """Reviewer-facing view."""

    id: int
    reference_code: str
    status: str
    applicant_name: str
    first_name: str
    middle_name: str | None
    last_name: str
    email: str
    contact_number: str | None
    school_id: int | None
    school_name_text: str | None
    school_label: str | None
    course: str | None
    year_level: str | None
    coordinator_name: str | None
    coordinator_email: str | None
    coordinator_contact: str | None
    required_hours: int | None
    ojt_start_date: date | None
    school_submission_deadline: date | None
    primary_technical_function: str | None
    emergency_contact_name: str | None
    emergency_contact_number: str | None
    emergency_contact_relation: str | None
    applicant_notes: str | None
    submitted_ip: str | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by_user_id: int | None
    review_notes: str | None
    created_intern_id: int | None
    # Set by the router when the submitted address already belongs to an
    # intern, so a duplicate is visible before approval rather than failing
    # during it.
    duplicate_of_intern_id: int | None = None


class RegistrationApprove(BaseModel):
    """Reviewer's corrections, applied when the intern record is created."""

    # Required when the applicant typed a school name: the reviewer either
    # points at an existing school or a new one they have just added.
    school_id: int | None = None
    team_id: int | None = None
    supervisor_id: int | None = None
    mentor_id: int | None = None
    department_id: int | None = None
    required_hours: int | None = Field(default=None, ge=1, le=10000)
    ojt_start_date: date | None = None
    school_submission_deadline: date | None = None
    primary_technical_function: str | None = Field(default=None, max_length=80)
    # Whether to also issue a sign-in account for the time clock.
    create_user_account: bool = True
    review_notes: str | None = Field(default=None, max_length=2000)


class RegistrationReject(BaseModel):
    # Required: a rejection with no reason is useless to the applicant and to
    # the next reviewer looking at the list.
    review_notes: str = Field(min_length=3, max_length=2000)


class RegistrationApproved(BaseModel):
    intern_id: int
    intern_code: str
    username: str | None = None
    temporary_password: str | None = None
    message: str
