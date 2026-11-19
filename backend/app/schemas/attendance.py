from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class PunchRequest(BaseModel):
    """Optional explicit timestamp; omitted means 'now'.

    Supervisors may back-date a punch; interns may not (enforced in the router).
    """
    timestamp: datetime | None = None
    remarks: str | None = Field(default=None, max_length=500)


class BreakRequest(BaseModel):
    timestamp: datetime | None = None
    break_type: str = "LUNCH"
    is_paid: bool = False


class ManualAttendanceRequest(BaseModel):
    intern_id: int
    business_date: date
    time_in: datetime | None = None
    time_out: datetime | None = None
    status_override: str | None = None
    overtime_approved: bool = False
    remarks: str | None = Field(default=None, max_length=1000)
    session_sequence: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_order(self):
        if self.time_in and self.time_out and self.time_out <= self.time_in:
            raise ValueError("time_out must be later than time_in.")
        return self


class BreakOut(ORMModel):
    id: int
    break_start: datetime
    break_end: datetime | None
    break_type: str
    is_paid: bool
    duration_minutes: int


class AttendanceOut(ORMModel):
    id: int
    intern_id: int
    business_date: date
    session_sequence: int
    time_in: datetime | None
    time_out: datetime | None

    shift_code: str | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    is_overnight_shift: bool

    scheduled_elapsed_minutes: int
    scheduled_break_minutes: int
    scheduled_net_minutes: int
    actual_elapsed_minutes: int
    actual_break_minutes: int
    unpaid_break_deducted_minutes: int
    actual_net_minutes: int

    late_minutes: int
    is_late: bool
    undertime_minutes: int
    overtime_minutes: int
    approved_overtime_minutes: int
    overtime_approved: bool

    creditable_minutes: int
    credit_capped: bool
    cap_reason: str | None

    status: str
    approval_status: str
    approved_by_id: int | None
    approved_at: datetime | None
    supervisor_remarks: str | None
    source: str
    is_locked: bool

    breaks: list[BreakOut] = []


class ApprovalRequest(BaseModel):
    remarks: str | None = Field(default=None, max_length=1000)
    approve_overtime: bool = False


class RejectionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CorrectionRequest(BaseModel):
    attendance_record_id: int | None = None
    intern_id: int
    business_date: date
    adjustment_type: str = Field(
        description="TIME_IN | TIME_OUT | BREAK | HOURS_CREDIT | STATUS | OVERTIME"
    )
    reason: str = Field(min_length=5, max_length=1000)
    requested_time_in: datetime | None = None
    requested_time_out: datetime | None = None
    requested_status: str | None = None
    requested_minutes_delta: int | None = None


class CorrectionOut(ORMModel):
    id: int
    attendance_record_id: int | None
    intern_id: int
    business_date: date
    adjustment_type: str
    reason: str
    requested_time_in: datetime | None
    requested_time_out: datetime | None
    requested_status: str | None
    requested_minutes_delta: int | None
    previous_time_in: datetime | None
    previous_time_out: datetime | None
    previous_status: str | None
    requested_by_id: int
    approval_status: str
    approved_by_id: int | None
    approved_at: datetime | None
    decision_remarks: str | None
