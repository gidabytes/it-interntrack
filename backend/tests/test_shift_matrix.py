"""Validation of the 17 standard hourly shift templates.

The specification names seven shifts for explicit verification and requires all
seventeen to be tested. Every template is driven through a full session so the
midnight rollover, break deduction, late calculation, overtime and OJT
crediting are exercised on each one.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from app.core.enums import AttendanceStatus
from app.core.timeutil import is_overnight, shift_elapsed_minutes
from app.services import attendance_service, ojt_engine
from app.services.schedule_resolver import find_shift_for_timestamp, resolve_for_date

pytestmark = pytest.mark.shift_matrix

MON = date(2026, 11, 16)

# code, start hour, end hour, overnight
EXPECTED_MATRIX = [
    ("SHIFT-01", 7, 16, False),
    ("SHIFT-02", 8, 17, False),
    ("SHIFT-03", 9, 18, False),
    ("SHIFT-04", 10, 19, False),
    ("SHIFT-05", 11, 20, False),
    ("SHIFT-06", 12, 21, False),
    ("SHIFT-07", 13, 22, False),
    ("SHIFT-08", 14, 23, False),
    ("SHIFT-09", 15, 0, True),
    ("SHIFT-10", 16, 1, True),
    ("SHIFT-11", 17, 2, True),
    ("SHIFT-12", 18, 3, True),
    ("SHIFT-13", 19, 4, True),
    ("SHIFT-14", 20, 5, True),
    ("SHIFT-15", 21, 6, True),
    ("SHIFT-16", 22, 7, True),
    ("SHIFT-17", 23, 8, True),
]

# The seven the specification calls out by name.
SPEC_NAMED = ["SHIFT-01", "SHIFT-02", "SHIFT-08", "SHIFT-09", "SHIFT-15",
              "SHIFT-16", "SHIFT-17"]


def test_all_seventeen_templates_exist(shifts):
    assert len(EXPECTED_MATRIX) == 17
    for code, _, _, _ in EXPECTED_MATRIX:
        assert code in shifts, f"{code} was not seeded"


@pytest.mark.parametrize("code,start_h,end_h,overnight", EXPECTED_MATRIX)
def test_template_definition(shifts, code, start_h, end_h, overnight):
    """Times, nine-hour duration, overnight flag and net working hours."""
    t = shifts[code]
    assert t.start_time == time(start_h, 0)
    assert t.end_time == time(end_h, 0)
    assert t.is_overnight is overnight
    assert t.elapsed_minutes == 540, "every standard shift is nine hours"
    assert t.break_minutes == 60
    assert t.break_is_paid is False
    assert t.unpaid_break_minutes == 60
    assert t.net_working_minutes == 480, "9h elapsed less 1h unpaid break"
    assert t.is_active is True
    assert t.is_system_default is True


@pytest.mark.parametrize("code,start_h,end_h,overnight", EXPECTED_MATRIX)
def test_full_session_on_each_template(
    db, rules, shifts, make_intern, punch, approve, code, start_h, end_h, overnight
):
    """Punch a complete shift on every template and verify the arithmetic.

    For an overnight template the time-out falls on the following calendar day,
    and the record must still be dated to the scheduled start date.
    """
    t = shifts[code]
    intern = make_intern(shift=t, work_days=(0,), start=MON)

    resolved = resolve_for_date(db, intern, MON)
    assert resolved.shift_code == code
    assert resolved.is_overnight is overnight
    assert resolved.scheduled_net_minutes == 480

    time_in = punch(MON, start_h, 0)
    out_day = MON + timedelta(days=1) if overnight else MON
    time_out = punch(out_day, end_h, 0)

    rec = attendance_service.time_in(db, intern, timestamp=time_in, rules=rules)
    assert rec.business_date == MON, (
        f"{code} must be dated to its scheduled start date"
    )
    rec = attendance_service.time_out(db, intern, timestamp=time_out, rules=rules)

    assert rec.is_overnight_shift is overnight
    assert rec.actual_elapsed_minutes == 540
    assert rec.unpaid_break_deducted_minutes == 60, "break deducted on every shift"
    assert rec.actual_net_minutes == 480
    assert rec.creditable_minutes == 480, "OJT crediting correct on every shift"
    assert rec.late_minutes == 0
    assert rec.undertime_minutes == 0
    assert rec.overtime_minutes == 0
    assert rec.status == AttendanceStatus.PRESENT

    # Exactly one record: never split at midnight.
    assert len(attendance_service.get_records_for_date(db, intern.id, MON)) == 1
    if overnight:
        assert attendance_service.get_records_for_date(db, intern.id, out_day) == []

    approve(rec, rules)
    assert ojt_engine.get_progress(db, intern).approved_minutes == 480


@pytest.mark.parametrize("code,start_h,end_h,overnight", EXPECTED_MATRIX)
def test_late_arrival_on_each_template(
    db, rules, shifts, make_intern, punch, code, start_h, end_h, overnight
):
    """A 30-minute late arrival is detected correctly on every template,
    including those whose start time is before midnight and whose end is after.
    """
    t = shifts[code]
    intern = make_intern(shift=t, work_days=(0,), start=MON)

    in_dt = punch(MON, start_h, 30)
    out_day = MON + timedelta(days=1) if overnight else MON
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=in_dt, time_out_at=punch(out_day, end_h, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.late_minutes == 30
    assert rec.is_late is True
    assert rec.status == AttendanceStatus.LATE
    assert rec.creditable_minutes == 450, "480 scheduled less the 30m missed"


@pytest.mark.parametrize("code", SPEC_NAMED)
def test_specification_named_shifts_overtime(
    db, rules, shifts, make_intern, punch, code
):
    """Approved overtime on each shift the specification names explicitly."""
    t = shifts[code]
    intern = make_intern(shift=t, work_days=(0,), start=MON)

    start_dt = punch(MON, t.start_time.hour, 0)
    # Work the full shift plus 60 minutes.
    end_dt = start_dt + timedelta(minutes=t.elapsed_minutes + 60)

    rec = attendance_service.manual_entry(
        db, intern, MON, time_in_at=start_dt, time_out_at=end_dt,
        actor_user_id=1, rules=rules, overtime_approved=True,
    )
    assert rec.overtime_minutes == 60
    assert rec.approved_overtime_minutes == 60
    assert rec.creditable_minutes == 540, "480 base + 60 approved overtime"
    assert rec.business_date == MON


@pytest.mark.parametrize("code,start_h,end_h,overnight", EXPECTED_MATRIX)
def test_punch_binding_window_on_each_template(
    db, rules, shifts, make_intern, punch, code, start_h, end_h, overnight
):
    """An early arrival binds to the right shift on every template.

    Arriving 30 minutes early must resolve to the shift about to start, which
    for the late-evening templates means resolving across the date boundary.
    """
    t = shifts[code]
    intern = make_intern(shift=t, work_days=(0,), start=MON)

    early = punch(MON, start_h, 0) - timedelta(minutes=30)
    resolved = find_shift_for_timestamp(db, intern, early)
    assert resolved is not None, f"{code}: early arrival did not bind"
    assert resolved.business_date == MON
    assert resolved.shift_code == code


def test_overnight_boundary_shift_09_ends_exactly_at_midnight(
    db, rules, shifts, make_intern, punch
):
    """SHIFT-09 (15:00-00:00) is the boundary case: it ends at 00:00 the next day.

    An end time of exactly midnight must be treated as overnight, or the shift
    would compute as a zero-length or negative duration.
    """
    t = shifts["SHIFT-09"]
    assert t.end_time == time(0, 0)
    assert t.is_overnight is True
    assert t.elapsed_minutes == 540

    intern = make_intern(shift=t, work_days=(0,), start=MON)
    resolved = resolve_for_date(db, intern, MON)
    assert resolved.scheduled_start.hour == 15
    assert resolved.scheduled_end.day == MON.day + 1
    assert resolved.scheduled_end.hour == 0

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 15, 0),
        time_out_at=punch(MON + timedelta(days=1), 0, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.business_date == MON
    assert rec.creditable_minutes == 480


def test_consecutive_overnight_shifts_do_not_merge(
    db, rules, shifts, make_intern, punch
):
    """Two nights in a row produce two records on two business dates."""
    intern = make_intern(shift=shifts["SHIFT-16"], work_days=(0, 1), start=MON)
    tue = MON + timedelta(days=1)
    wed = MON + timedelta(days=2)

    attendance_service.time_in(db, intern, timestamp=punch(MON, 22, 0), rules=rules)
    attendance_service.time_out(db, intern, timestamp=punch(tue, 7, 0), rules=rules)
    attendance_service.time_in(db, intern, timestamp=punch(tue, 22, 0), rules=rules)
    attendance_service.time_out(db, intern, timestamp=punch(wed, 7, 0), rules=rules)

    mon_records = attendance_service.get_records_for_date(db, intern.id, MON)
    tue_records = attendance_service.get_records_for_date(db, intern.id, tue)
    assert len(mon_records) == 1
    assert len(tue_records) == 1
    assert mon_records[0].creditable_minutes == 480
    assert tue_records[0].creditable_minutes == 480
    assert ojt_engine.get_progress(db, intern).pending_minutes == 960


def test_all_seventeen_in_one_week_for_different_interns(
    db, rules, shifts, make_intern, punch, approve
):
    """Seventeen interns, one per shift, all working the same business date.

    This is the 24/7 coverage case: every template active simultaneously, each
    crediting exactly eight hours.
    """
    total = 0
    for code, start_h, end_h, overnight in EXPECTED_MATRIX:
        intern = make_intern(
            first_name=code.replace("-", ""), last_name="Tester",
            shift=shifts[code], work_days=(0,), start=MON,
        )
        out_day = MON + timedelta(days=1) if overnight else MON
        rec = attendance_service.manual_entry(
            db, intern, MON,
            time_in_at=punch(MON, start_h, 0),
            time_out_at=punch(out_day, end_h, 0),
            actor_user_id=1, rules=rules,
        )
        approve(rec, rules)
        assert rec.business_date == MON
        assert rec.creditable_minutes == 480
        total += rec.creditable_minutes

    assert total == 17 * 480, "all seventeen shifts credited eight hours each"
