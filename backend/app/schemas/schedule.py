from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, Field, model_validator

from app.core.enums import AssignmentType
from app.schemas.common import ORMModel


class ShiftTemplateCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=120)
    start_time: time
    end_time: time
    break_minutes: int = Field(default=60, ge=0, le=480)
    break_is_paid: bool = False
    grace_period_minutes: int = Field(default=15, ge=0, le=240)
    absence_grace_minutes: int = Field(default=60, ge=0, le=720)
    max_overtime_minutes: int = Field(default=120, ge=0, le=720)
    min_rest_hours: int = Field(default=8, ge=0, le=48)
    is_active: bool = True
    effective_from: date | None = None
    effective_to: date | None = None
    remarks: str | None = None


class ShiftTemplateUpdate(BaseModel):
    name: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=480)
    break_is_paid: bool | None = None
    grace_period_minutes: int | None = Field(default=None, ge=0, le=240)
    absence_grace_minutes: int | None = Field(default=None, ge=0, le=720)
    max_overtime_minutes: int | None = Field(default=None, ge=0, le=720)
    min_rest_hours: int | None = Field(default=None, ge=0, le=48)
    is_active: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remarks: str | None = None


class ShiftTemplateOut(ORMModel):
    id: int
    code: str
    name: str
    start_time: time
    end_time: time
    is_overnight: bool
    elapsed_minutes: int
    break_minutes: int
    break_is_paid: bool
    unpaid_break_minutes: int
    net_working_minutes: int
    grace_period_minutes: int
    absence_grace_minutes: int
    max_overtime_minutes: int
    min_rest_hours: int
    is_active: bool
    is_system_default: bool
    effective_from: date | None
    effective_to: date | None


class AssignmentCreate(BaseModel):
    intern_id: int
    shift_template_id: int | None = None
    assignment_type: str = AssignmentType.WEEKLY_RECURRING
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    specific_date: date | None = None
    rotation_cycle_weeks: int | None = Field(default=None, ge=1, le=12)
    rotation_week_index: int | None = Field(default=None, ge=0, le=11)
    rotation_anchor_date: date | None = None
    is_rest_day: bool = False
    effective_from: date
    effective_to: date | None = None
    remarks: str | None = None
    # Supervisor acknowledgement when saving despite a conflict warning.
    conflict_override_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate(self):
        if not self.is_rest_day and self.shift_template_id is None:
            raise ValueError(
                "shift_template_id is required unless is_rest_day is true."
            )
        if self.assignment_type == AssignmentType.DAILY_OVERRIDE and not self.specific_date:
            raise ValueError("DAILY_OVERRIDE requires specific_date.")
        if self.assignment_type == AssignmentType.ROTATING:
            if self.rotation_cycle_weeks is None or self.rotation_week_index is None:
                raise ValueError(
                    "ROTATING requires rotation_cycle_weeks and rotation_week_index."
                )
            if self.rotation_week_index >= self.rotation_cycle_weeks:
                raise ValueError(
                    "rotation_week_index must be less than rotation_cycle_weeks."
                )
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from.")
        return self


class BulkAssignmentCreate(BaseModel):
    intern_ids: list[int] = Field(min_length=1, max_length=500)
    shift_template_id: int | None = None
    assignment_type: str = AssignmentType.WEEKLY_RECURRING
    days_of_week: list[int] = Field(default_factory=list)
    rest_days_of_week: list[int] = Field(default_factory=list)
    effective_from: date
    effective_to: date | None = None
    rotation_cycle_weeks: int | None = None
    rotation_week_index: int | None = None
    rotation_anchor_date: date | None = None
    allow_conflicts: bool = False
    conflict_override_reason: str | None = None


class AssignmentOut(ORMModel):
    id: int
    intern_id: int
    shift_template_id: int | None
    assignment_type: str
    day_of_week: int | None
    specific_date: date | None
    rotation_cycle_weeks: int | None
    rotation_week_index: int | None
    rotation_anchor_date: date | None
    is_rest_day: bool
    effective_from: date
    effective_to: date | None
    approval_status: str
    approved_by_id: int | None
    conflict_override_reason: str | None
    remarks: str | None


class HolidayCreate(BaseModel):
    holiday_date: date
    name: str = Field(min_length=2, max_length=180)
    holiday_type: str = "REGULAR"
    department_id: int | None = None
    is_non_working: bool = True
    is_creditable: bool = False


class RestDayCreate(BaseModel):
    intern_id: int
    day_of_week: int = Field(ge=0, le=6)
    effective_from: date
    effective_to: date | None = None
