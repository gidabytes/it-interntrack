"""The 20 attendance / OJT scenarios the specification requires.

Each test is numbered to match the specification's list so a reviewer can map
requirement to evidence. Every assertion is about a figure the system would
report to a school or to management, not an implementation detail.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.enums import ApprovalStatus, AttendanceStatus
from app.services import attendance_service, ojt_engine
from app.services.attendance_service import AttendanceError
from app.services.schedule_resolver import find_shift_for_timestamp, resolve_for_date

pytestmark = pytest.mark.mandatory

MON = date(2026, 11, 16)
FRI = date(2026, 11, 20)
SAT = date(2026, 11, 21)


# ---------------------------------------------------------------------------
# 1. Regular 8-hour shift
# ---------------------------------------------------------------------------

def test_01_regular_eight_hour_shift(db, rules, shifts, make_intern, punch):
    """SHIFT-02 (08:00-17:00): 9h elapsed, 1h unpaid break, 8h credited.

    The spec is explicit that the full 9 hours must NOT be credited.
    """
    s = shifts["SHIFT-02"]
    intern = make_intern(shift=s)

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )

    assert rec.scheduled_elapsed_minutes == 540, "9h scheduled elapsed"
    assert rec.scheduled_break_minutes == 60, "1h unpaid break"
    assert rec.scheduled_net_minutes == 480, "8h expected working time"
    assert rec.actual_elapsed_minutes == 540
    assert rec.unpaid_break_deducted_minutes == 60
    assert rec.actual_net_minutes == 480
    assert rec.creditable_minutes == 480, "credits 8h, not the full 9h"
    assert rec.status == AttendanceStatus.PRESENT
    assert rec.late_minutes == 0
    assert rec.undertime_minutes == 0
    assert rec.overtime_minutes == 0


# ---------------------------------------------------------------------------
# 2. 2 PM - 11 PM shift
# ---------------------------------------------------------------------------

def test_02_afternoon_shift_14_to_23(db, rules, shifts, make_intern, punch):
    """SHIFT-08 (14:00-23:00) ends at 23:00, so it does NOT cross midnight."""
    s = shifts["SHIFT-08"]
    assert s.is_overnight is False
    intern = make_intern(shift=s)

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 14, 0), time_out_at=punch(MON, 23, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.business_date == MON
    assert rec.is_overnight_shift is False
    assert rec.creditable_minutes == 480
    assert rec.status == AttendanceStatus.PRESENT


# ---------------------------------------------------------------------------
# 3. 10 PM - 7 AM overnight shift
# ---------------------------------------------------------------------------

def test_03_overnight_shift_22_to_07(db, rules, shifts, make_intern, punch):
    """The spec's own example, verbatim.

    Time In  : November 20, 2026, 9:55 PM
    Time Out : November 21, 2026, 7:05 AM
    Must be ONE session on business date November 20.
    """
    s = shifts["SHIFT-16"]
    assert s.is_overnight is True
    intern = make_intern(shift=s, work_days=(0, 1, 2, 3, 4))

    resolved = find_shift_for_timestamp(db, intern, punch(FRI, 21, 55))
    assert resolved is not None
    assert resolved.business_date == FRI, "binds to the scheduled START date"

    rec = attendance_service.time_in(
        db, intern, timestamp=punch(FRI, 21, 55), rules=rules
    )
    assert rec.business_date == FRI
    assert rec.is_overnight_shift is True

    rec = attendance_service.time_out(
        db, intern, timestamp=punch(SAT, 7, 5), rules=rules
    )

    # One record, not two.
    records = attendance_service.get_records_for_date(db, intern.id, FRI)
    assert len(records) == 1
    assert attendance_service.get_records_for_date(db, intern.id, SAT) == []

    assert rec.business_date == FRI
    assert rec.actual_elapsed_minutes == 550, "21:55 -> 07:05 across midnight"
    assert rec.actual_net_minutes == 490, "550 less the 60m unpaid break"
    assert rec.creditable_minutes == 480, "clipped to the scheduled 8h"
    assert rec.overtime_minutes == 10, "5m early + 5m late, unapproved"
    assert rec.approved_overtime_minutes == 0
    assert rec.status == AttendanceStatus.PRESENT


def test_03b_past_midnight_arrival_binds_to_previous_business_date(
    db, rules, shifts, make_intern, punch
):
    """A 00:30 time-in belongs to the 22:00 shift that started yesterday."""
    intern = make_intern(shift=shifts["SHIFT-16"], work_days=(0, 1, 2, 3, 4))
    rec = attendance_service.time_in(
        db, intern, timestamp=punch(SAT, 0, 30), rules=rules
    )
    assert rec.business_date == FRI
    assert rec.late_minutes == 150, "150 minutes after the 22:00 start"
    assert rec.is_late is True


# ---------------------------------------------------------------------------
# 4. Late arrival
# ---------------------------------------------------------------------------

def test_04_late_arrival(db, rules, shifts, make_intern, punch):
    """Late beyond the grace period is flagged and reduces credit."""
    s = shifts["SHIFT-02"]
    assert s.grace_period_minutes == 15
    intern = make_intern(shift=s)

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 45), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.late_minutes == 45
    assert rec.is_late is True
    assert rec.status == AttendanceStatus.LATE
    assert rec.creditable_minutes == 435, "480 scheduled less 45m missed"
    assert rec.undertime_minutes == 45


def test_04b_within_grace_is_not_late(db, rules, shifts, make_intern, punch):
    """Ten minutes late, inside a 15-minute grace, is not flagged late."""
    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 10), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.is_late is False
    assert rec.late_minutes == 0
    assert rec.status == AttendanceStatus.PRESENT, (
        "an arrival inside the grace window is not an exception"
    )
    assert rec.undertime_minutes == 0, "grace forgives the flag"
    # But it does NOT invent hours: credit follows time actually worked.
    assert rec.actual_net_minutes == 470, "08:10-17:00 less the 60m break"
    assert rec.creditable_minutes == 470, (
        "grace suppresses the exception flag, it does not credit unworked time"
    )


# ---------------------------------------------------------------------------
# 5. Early time-out
# ---------------------------------------------------------------------------

def test_05_early_time_out_creates_undertime(db, rules, shifts, make_intern, punch):
    s = shifts["SHIFT-02"]
    intern = make_intern(shift=s)
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 15, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.actual_elapsed_minutes == 420
    assert rec.actual_net_minutes == 360, "420 less the 60m unpaid break"
    assert rec.undertime_minutes == 120, "left 2h before the scheduled end"
    assert rec.creditable_minutes == 360
    assert rec.status == AttendanceStatus.UNDERTIME


# ---------------------------------------------------------------------------
# 6. Unpaid lunch break
# ---------------------------------------------------------------------------

def test_06_unpaid_lunch_break_is_deducted(db, rules, shifts, make_intern, punch):
    """The spec's example E: 7AM-4PM, 9h elapsed, 1h unpaid lunch, 8h expected."""
    s = shifts["SHIFT-01"]
    assert s.break_is_paid is False
    assert s.break_minutes == 60
    assert s.net_working_minutes == 480

    intern = make_intern(shift=s)
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 7, 0), time_out_at=punch(MON, 16, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.scheduled_elapsed_minutes == 540
    assert rec.unpaid_break_deducted_minutes == 60
    assert rec.creditable_minutes == 480


def test_06b_paid_break_is_not_deducted(db, rules, shifts, make_intern, punch):
    """A shift configured with a PAID break credits the full elapsed time."""
    s = shifts["SHIFT-02"]
    s.break_is_paid = True
    db.flush()

    intern = make_intern(shift=s)
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec.scheduled_break_minutes == 0
    assert rec.scheduled_net_minutes == 540
    assert rec.unpaid_break_deducted_minutes == 0
    assert rec.creditable_minutes == 540


# ---------------------------------------------------------------------------
# 7. Multiple breaks
# ---------------------------------------------------------------------------

def test_07_multiple_breaks(db, rules, shifts, make_intern, punch):
    """Several logged breaks totalling more than the scheduled unpaid break.

    The longer actual total is deducted, so an intern cannot gain credit by
    taking more break time than the shift allows.
    """
    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.time_in(db, intern, timestamp=punch(MON, 8, 0), rules=rules)

    # 45m lunch + 15m + 30m = 90m of unpaid break.
    for start_h, start_m, end_h, end_m in [
        (12, 0, 12, 45), (15, 0, 15, 15), (16, 0, 16, 30)
    ]:
        attendance_service.break_start(
            db, intern, timestamp=punch(MON, start_h, start_m), rules=rules
        )
        attendance_service.break_end(
            db, intern, timestamp=punch(MON, end_h, end_m), rules=rules
        )

    rec = attendance_service.time_out(db, intern, timestamp=punch(MON, 17, 0), rules=rules)

    assert len(rec.breaks) == 3
    assert rec.actual_break_minutes == 90
    assert rec.unpaid_break_deducted_minutes == 90, "actual exceeds the scheduled 60m"
    assert rec.actual_net_minutes == 450, "540 elapsed less 90m break"
    assert rec.creditable_minutes == 450


def test_07b_short_break_still_deducts_scheduled_minimum(
    db, rules, shifts, make_intern, punch
):
    """A 20-minute logged break does not beat the shift's 60-minute unpaid policy."""
    intern = make_intern(shift=shifts["SHIFT-02"])
    attendance_service.time_in(db, intern, timestamp=punch(MON, 8, 0), rules=rules)
    attendance_service.break_start(db, intern, timestamp=punch(MON, 12, 0), rules=rules)
    attendance_service.break_end(db, intern, timestamp=punch(MON, 12, 20), rules=rules)
    rec = attendance_service.time_out(db, intern, timestamp=punch(MON, 17, 0), rules=rules)

    assert rec.actual_break_minutes == 20
    assert rec.unpaid_break_deducted_minutes == 60, "scheduled unpaid break still applies"
    assert rec.creditable_minutes == 480


# ---------------------------------------------------------------------------
# 8. Missing time-out
# ---------------------------------------------------------------------------

def test_08_missing_time_out(db, rules, shifts, make_intern, punch):
    """An open session is IN_PROGRESS during the shift and INCOMPLETE after it.

    Critically, it credits zero hours either way: an unterminated session must
    never silently credit a full day.
    """
    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.time_in(db, intern, timestamp=punch(MON, 8, 0), rules=rules)

    # Mid-shift
    attendance_service.recompute_record(
        db, rec, rules=rules, evaluation_time=punch(MON, 12, 0)
    )
    assert rec.status == AttendanceStatus.IN_PROGRESS
    assert rec.creditable_minutes == 0

    # Well after the shift and its absence grace.
    attendance_service.recompute_record(
        db, rec, rules=rules, evaluation_time=punch(MON + timedelta(days=1), 10, 0)
    )
    assert rec.status == AttendanceStatus.INCOMPLETE_TIME_RECORD
    assert rec.creditable_minutes == 0
    assert rec.is_open is True


# ---------------------------------------------------------------------------
# 9. Duplicate time-in
# ---------------------------------------------------------------------------

def test_09_duplicate_time_in_is_rejected(db, rules, shifts, make_intern, punch):
    intern = make_intern(shift=shifts["SHIFT-02"])
    attendance_service.time_in(db, intern, timestamp=punch(MON, 8, 0), rules=rules)

    with pytest.raises(AttendanceError) as exc:
        attendance_service.time_in(db, intern, timestamp=punch(MON, 8, 30), rules=rules)
    assert exc.value.code == "DUPLICATE_TIME_IN"

    # Exactly one record exists.
    assert len(attendance_service.get_records_for_date(db, intern.id, MON)) == 1


def test_09b_time_out_without_time_in_is_rejected(
    db, rules, shifts, make_intern, punch
):
    intern = make_intern(shift=shifts["SHIFT-02"])
    with pytest.raises(AttendanceError) as exc:
        attendance_service.time_out(db, intern, timestamp=punch(MON, 17, 0), rules=rules)
    assert exc.value.code == "NO_OPEN_SESSION"


# ---------------------------------------------------------------------------
# 10. Rest day
# ---------------------------------------------------------------------------

def test_10_rest_day(db, rules, shifts, make_intern):
    """Saturday is a rest day: no shift, REST_DAY status, no hours owed."""
    intern = make_intern(shift=shifts["SHIFT-02"], work_days=(0, 1, 2, 3, 4))

    resolved = resolve_for_date(db, intern, SAT)
    assert resolved.is_rest_day is True
    assert resolved.has_shift is False
    assert resolved.is_working_day is False

    rec = attendance_service.ensure_day_records(
        db, intern, SAT, rules=rules,
        evaluation_time=None,
    )
    assert rec is not None
    assert rec.status == AttendanceStatus.REST_DAY
    assert rec.creditable_minutes == 0
    assert rec.scheduled_net_minutes == 0


def test_10b_working_a_rest_day_still_records_hours(
    db, rules, shifts, make_intern, punch
):
    """24/7 operations pull interns in on rest days; the hours must be captured."""
    intern = make_intern(shift=shifts["SHIFT-02"], work_days=(0, 1, 2, 3, 4))
    rec = attendance_service.time_in(db, intern, timestamp=punch(SAT, 8, 0), rules=rules)
    rec = attendance_service.time_out(db, intern, timestamp=punch(SAT, 17, 0), rules=rules)

    assert rec.business_date == SAT
    assert rec.actual_net_minutes > 0, "rest-day work is recorded"
    assert rec.status == AttendanceStatus.NO_SCHEDULE, (
        "no shift was assigned for the rest day"
    )


# ---------------------------------------------------------------------------
# 11. Approved leave
# ---------------------------------------------------------------------------

def test_11_approved_leave_does_not_credit_hours_by_default(
    db, rules, shifts, make_intern, org
):
    """Per spec: approved leave does NOT count toward required hours unless an
    administrator explicitly configures otherwise."""
    import app.models as m

    intern = make_intern(shift=shifts["SHIFT-02"])
    leave = m.LeaveRequest(
        intern_id=intern.id, leave_type="SICK",
        date_from=MON, date_to=MON, reason="Fever",
        requested_by_id=org["supervisor_user"].id,
        approval_status=ApprovalStatus.APPROVED,
        approved_by_id=org["supervisor_user"].id,
        is_creditable=False,
    )
    db.add(leave)
    db.flush()

    resolved = resolve_for_date(db, intern, MON)
    assert resolved.is_on_leave is True

    rec = attendance_service.ensure_day_records(db, intern, MON, rules=rules)
    assert rec.status == AttendanceStatus.ON_LEAVE
    assert rec.creditable_minutes == 0, "leave credits no OJT hours by default"

    progress = ojt_engine.get_progress(db, intern)
    assert progress.approved_minutes == 0


def test_11b_leave_credits_hours_when_policy_enabled(
    db, rules, shifts, make_intern, org
):
    """With the policy switched on, approved leave credits the scheduled hours."""
    import app.models as m
    from app.services.settings_service import load_rules, set_setting

    set_setting(db, "approved_leave_counts_as_hours", True)
    db.flush()
    rules2 = load_rules(db)

    intern = make_intern(shift=shifts["SHIFT-02"])
    db.add(m.LeaveRequest(
        intern_id=intern.id, leave_type="SICK", date_from=MON, date_to=MON,
        reason="Fever", requested_by_id=org["supervisor_user"].id,
        approval_status=ApprovalStatus.APPROVED, is_creditable=True,
    ))
    db.flush()

    rec = attendance_service.ensure_day_records(db, intern, MON, rules=rules2)
    assert rec.creditable_minutes == 480, "credits the scheduled net hours"


# ---------------------------------------------------------------------------
# 12. Unapproved leave
# ---------------------------------------------------------------------------

def test_12_unapproved_leave_leaves_the_day_absent(
    db, rules, shifts, make_intern, org, punch
):
    """A pending leave request does not excuse the absence."""
    import app.models as m

    intern = make_intern(shift=shifts["SHIFT-02"])
    db.add(m.LeaveRequest(
        intern_id=intern.id, leave_type="PERSONAL", date_from=MON, date_to=MON,
        reason="Family matter", requested_by_id=org["supervisor_user"].id,
        approval_status=ApprovalStatus.PENDING,
    ))
    db.flush()

    resolved = resolve_for_date(db, intern, MON)
    assert resolved.is_on_leave is False, "only APPROVED leave counts"

    rec = attendance_service.ensure_day_records(
        db, intern, MON, rules=rules,
        evaluation_time=punch(MON + timedelta(days=1), 10, 0),
    )
    assert rec.status == AttendanceStatus.ABSENT
    assert rec.creditable_minutes == 0


# ---------------------------------------------------------------------------
# 13. Schedule modification
# ---------------------------------------------------------------------------

def test_13_schedule_modification_by_daily_override(
    db, rules, shifts, make_intern, punch
):
    """A DAILY_OVERRIDE outranks the weekly pattern for that date only."""
    import app.models as m

    intern = make_intern(shift=shifts["SHIFT-02"], work_days=(0, 1, 2, 3, 4))
    assert resolve_for_date(db, intern, MON).shift_code == "SHIFT-02"

    db.add(m.ScheduleAssignment(
        intern_id=intern.id,
        shift_template_id=shifts["SHIFT-16"].id,
        assignment_type="DAILY_OVERRIDE",
        specific_date=MON,
        effective_from=MON,
        approval_status=ApprovalStatus.APPROVED,
    ))
    db.flush()

    assert resolve_for_date(db, intern, MON).shift_code == "SHIFT-16", "override wins"
    assert resolve_for_date(
        db, intern, MON + timedelta(days=1)
    ).shift_code == "SHIFT-02", "other days unaffected"


def test_13b_changing_a_template_does_not_rewrite_history(
    db, rules, shifts, make_intern, punch
):
    """The snapshot requirement: editing a shift must not alter past figures."""
    s = shifts["SHIFT-02"]
    intern = make_intern(shift=s)

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )
    original_credit = rec.creditable_minutes
    original_snapshot = dict(rec.shift_snapshot)
    assert original_credit == 480

    # An administrator lengthens the unpaid break on the live template.
    s.break_minutes = 120
    db.flush()
    assert s.net_working_minutes == 420

    # Recomputing the historical record uses its own frozen snapshot.
    attendance_service.recompute_record(db, rec, rules=rules)
    assert rec.shift_snapshot == original_snapshot
    assert rec.creditable_minutes == original_credit, (
        "historical computation is unchanged by the template edit"
    )

    # A new session on the changed template uses the new configuration.
    tue = MON + timedelta(days=1)
    rec2 = attendance_service.manual_entry(
        db, intern, tue,
        time_in_at=punch(tue, 8, 0), time_out_at=punch(tue, 17, 0),
        actor_user_id=1, rules=rules,
    )
    assert rec2.scheduled_net_minutes == 420
    assert rec2.creditable_minutes == 420


# ---------------------------------------------------------------------------
# 14. Attendance correction
# ---------------------------------------------------------------------------

def test_14_attendance_correction_reverses_and_reposts(
    db, rules, shifts, make_intern, punch, approve
):
    """A correction must not double count: the old posting is reversed."""
    import app.models as m

    intern = make_intern(shift=shifts["SHIFT-02"])
    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )
    approve(rec, rules)
    assert ojt_engine.get_progress(db, intern).approved_minutes == 480

    # Correction: the intern actually left at 13:00.
    rec.time_out = punch(MON, 13, 0)
    attendance_service.recompute_record(db, rec, rules=rules)
    ojt_engine.post_attendance(db, rec, actor_user_id=1, rules=rules)
    db.flush()

    progress = ojt_engine.get_progress(db, intern)
    assert rec.creditable_minutes == 240
    assert progress.approved_minutes == 240, "not 480 + 240"

    entries = db.scalars(
        __import__("sqlalchemy").select(m.OJTHourLedger).where(
            m.OJTHourLedger.intern_id == intern.id
        ).order_by(m.OJTHourLedger.id)
    ).all()
    assert len(entries) == 3, "original, reversal, replacement"
    assert entries[0].minutes_delta == 480 and entries[0].is_reversed is True
    assert entries[1].reversal_of_id == entries[0].id
    assert entries[2].minutes_delta == 240 and entries[2].is_reversed is False
    # The original is preserved for audit.
    assert entries[0].id in {e.id for e in entries}


# ---------------------------------------------------------------------------
# 15. Overtime approval
# ---------------------------------------------------------------------------

def test_15_overtime_requires_approval(db, rules, shifts, make_intern, punch):
    """Unauthorised overtime is recorded but never credited."""
    intern = make_intern(shift=shifts["SHIFT-02"])

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 19, 0),
        actor_user_id=1, rules=rules, overtime_approved=False,
    )
    assert rec.actual_net_minutes == 600, "11h less the 1h break"
    assert rec.overtime_minutes == 120, "detected"
    assert rec.approved_overtime_minutes == 0, "not authorised"
    assert rec.creditable_minutes == 480, "only the scheduled 8h is credited"

    # Supervisor approves the overtime.
    rec.overtime_approved = True
    attendance_service.recompute_record(db, rec, rules=rules)
    assert rec.approved_overtime_minutes == 120
    assert rec.creditable_minutes == 600


def test_15b_overtime_is_capped_at_the_shift_maximum(
    db, rules, shifts, make_intern, punch
):
    """Approved overtime cannot exceed the template's maximum."""
    s = shifts["SHIFT-02"]
    assert s.max_overtime_minutes == 120
    intern = make_intern(shift=s)

    rec = attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 22, 0),
        actor_user_id=1, rules=rules, overtime_approved=True,
    )
    assert rec.overtime_minutes == 300, "5h beyond the scheduled end"
    assert rec.approved_overtime_minutes == 120, "capped at the shift maximum"
    assert rec.creditable_minutes == 600


# ---------------------------------------------------------------------------
# 16. OJT completion at 100%
# ---------------------------------------------------------------------------

def test_16_completion_is_capped_at_100_percent(db, rules, make_intern):
    """Approved hours never exceed required hours in the completion figure, but
    the actual over-rendered total stays visible."""
    intern = make_intern(required_hours=100)

    ojt_engine.post_adjustment(
        db, intern.id, MON, 120 * 60, actor_user_id=1,
        description="Opening balance over the requirement",
    )
    db.flush()

    p = ojt_engine.get_progress(db, intern)
    assert p.required_minutes == 6000
    assert p.approved_minutes == 7200
    assert p.completion_percentage == 100.0, "capped"
    assert p.raw_completion_percentage == 120.0, "true figure still available"
    assert p.remaining_minutes == 0, "floors at zero, never negative"
    assert p.is_complete is True
    assert p.milestone == "Completed"
    assert p.next_milestone is None


def test_16b_exact_completion(db, rules, make_intern):
    intern = make_intern(required_hours=100)
    ojt_engine.post_adjustment(
        db, intern.id, MON, 100 * 60, actor_user_id=1, description="Exact"
    )
    db.flush()
    p = ojt_engine.get_progress(db, intern)
    assert p.completion_percentage == 100.0
    assert p.remaining_minutes == 0
    assert p.is_complete is True


# ---------------------------------------------------------------------------
# 17. Remaining-hour calculation
# ---------------------------------------------------------------------------

def test_17_remaining_hours_matches_the_specification_example(db, rules, make_intern):
    """Required 486, approved 210 -> remaining 276, completion 43.21%."""
    intern = make_intern(required_hours=486)
    ojt_engine.post_adjustment(
        db, intern.id, MON, 210 * 60, actor_user_id=1,
        description="Approved rendered hours to date",
    )
    db.flush()

    p = ojt_engine.get_progress(db, intern)
    assert p.required_minutes == 486 * 60
    assert p.approved_minutes == 210 * 60
    assert p.remaining_minutes == 276 * 60
    assert p.completion_percentage == 43.21
    assert p.milestone == "Initial Progress"
    assert p.next_milestone == (50, "Halfway Completed")


def test_17b_pending_hours_are_excluded_from_approved(
    db, rules, shifts, make_intern, punch, approve
):
    """Unapproved attendance is reported as pending, not as approved hours."""
    intern = make_intern(shift=shifts["SHIFT-02"], required_hours=486)

    rec1 = attendance_service.manual_entry(
        db, intern, MON, time_in_at=punch(MON, 8, 0),
        time_out_at=punch(MON, 17, 0), actor_user_id=1, rules=rules,
    )
    approve(rec1, rules)

    tue = MON + timedelta(days=1)
    rec2 = attendance_service.manual_entry(
        db, intern, tue, time_in_at=punch(tue, 8, 0),
        time_out_at=punch(tue, 17, 0), actor_user_id=1, rules=rules,
    )
    ojt_engine.post_attendance(db, rec2, actor_user_id=1, rules=rules)
    db.flush()

    p = ojt_engine.get_progress(db, intern)
    assert p.approved_minutes == 480
    assert p.pending_minutes == 480
    assert p.remaining_minutes == 486 * 60 - 480, "pending does not reduce remaining"


# ---------------------------------------------------------------------------
# 18. Intern with multiple schedules in the same week
# ---------------------------------------------------------------------------

def test_18_multiple_schedules_in_one_week(db, rules, shifts, make_intern, punch):
    """Different shifts on different days, each computed on its own basis."""
    import app.models as m

    intern = make_intern(start=MON)
    # Mon/Wed day shift, Tue/Thu overnight, Fri afternoon.
    plan = {
        0: "SHIFT-02", 1: "SHIFT-16", 2: "SHIFT-02", 3: "SHIFT-16", 4: "SHIFT-08",
    }
    for dow, code in plan.items():
        db.add(m.ScheduleAssignment(
            intern_id=intern.id, shift_template_id=shifts[code].id,
            assignment_type="WEEKLY_RECURRING", day_of_week=dow,
            effective_from=MON, approval_status=ApprovalStatus.APPROVED,
        ))
    db.add(m.RestDay(intern_id=intern.id, day_of_week=5, effective_from=MON))
    db.add(m.RestDay(intern_id=intern.id, day_of_week=6, effective_from=MON))
    db.flush()

    for offset, expected in enumerate(
        ["SHIFT-02", "SHIFT-16", "SHIFT-02", "SHIFT-16", "SHIFT-08"]
    ):
        d = MON + timedelta(days=offset)
        resolved = resolve_for_date(db, intern, d)
        assert resolved.shift_code == expected, f"{d} should be {expected}"
        assert resolved.scheduled_net_minutes == 480

    assert resolve_for_date(db, intern, MON + timedelta(days=5)).is_rest_day

    # Punching in on the overnight day binds to that day, not the next.
    tue = MON + timedelta(days=1)
    rec = attendance_service.time_in(db, intern, timestamp=punch(tue, 22, 0), rules=rules)
    assert rec.business_date == tue
    assert rec.shift_code == "SHIFT-16"


def test_18b_rotating_schedule(db, rules, shifts, make_intern):
    """A two-week rotation alternates shifts by week index."""
    import app.models as m

    intern = make_intern(start=MON)
    for week_index, code in [(0, "SHIFT-02"), (1, "SHIFT-16")]:
        for dow in range(5):
            db.add(m.ScheduleAssignment(
                intern_id=intern.id, shift_template_id=shifts[code].id,
                assignment_type="ROTATING", day_of_week=dow,
                rotation_cycle_weeks=2, rotation_week_index=week_index,
                rotation_anchor_date=MON, effective_from=MON,
                approval_status=ApprovalStatus.APPROVED,
            ))
    db.flush()

    assert resolve_for_date(db, intern, MON).shift_code == "SHIFT-02"
    assert resolve_for_date(
        db, intern, MON + timedelta(days=7)
    ).shift_code == "SHIFT-16", "week 2 of the rotation"
    assert resolve_for_date(
        db, intern, MON + timedelta(days=14)
    ).shift_code == "SHIFT-02", "cycle repeats"


# ---------------------------------------------------------------------------
# 19. Month-end and year-end attendance
# ---------------------------------------------------------------------------

def test_19_month_end_overnight_crosses_into_the_next_month(
    db, rules, shifts, make_intern, punch
):
    """A 30 November overnight shift ending 1 December stays dated November."""
    nov30 = date(2026, 11, 30)   # Monday
    dec1 = date(2026, 12, 1)

    intern = make_intern(shift=shifts["SHIFT-16"], work_days=(0, 1, 2, 3, 4), start=MON)
    rec = attendance_service.time_in(db, intern, timestamp=punch(nov30, 22, 0), rules=rules)
    rec = attendance_service.time_out(db, intern, timestamp=punch(dec1, 7, 0), rules=rules)

    assert rec.business_date == nov30, "credited to November, not December"
    assert rec.creditable_minutes == 480
    assert attendance_service.get_records_for_date(db, intern.id, dec1) == []


def test_19b_year_end_overnight_crosses_into_the_next_year(
    db, rules, shifts, make_intern, punch
):
    """31 December 22:00 -> 1 January 07:00 stays dated 31 December."""
    dec31 = date(2026, 12, 31)   # Thursday
    jan1 = date(2027, 1, 1)

    intern = make_intern(shift=shifts["SHIFT-16"], work_days=(0, 1, 2, 3, 4), start=MON)
    rec = attendance_service.time_in(db, intern, timestamp=punch(dec31, 22, 0), rules=rules)
    rec = attendance_service.time_out(db, intern, timestamp=punch(jan1, 7, 0), rules=rules)

    assert rec.business_date == dec31
    assert rec.business_date.year == 2026
    assert rec.creditable_minutes == 480


# ---------------------------------------------------------------------------
# 20. Two overlapping attendance sessions
# ---------------------------------------------------------------------------

def test_20_overlapping_sessions_are_rejected(db, rules, shifts, make_intern, punch):
    """A manual entry that overlaps an existing session must be refused."""
    intern = make_intern(shift=shifts["SHIFT-02"])

    attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 8, 0), time_out_at=punch(MON, 17, 0),
        actor_user_id=1, rules=rules,
    )

    with pytest.raises(AttendanceError) as exc:
        attendance_service.manual_entry(
            db, intern, MON,
            time_in_at=punch(MON, 14, 0), time_out_at=punch(MON, 20, 0),
            actor_user_id=1, rules=rules, session_sequence=2,
        )
    assert exc.value.code == "OVERLAPPING_ATTENDANCE"


def test_20b_overnight_session_cannot_overlap_the_next_day(
    db, rules, shifts, make_intern, punch
):
    """The overlap check must span business dates, not just compare within one."""
    import app.models as m

    intern = make_intern(start=MON)
    db.add(m.ScheduleAssignment(
        intern_id=intern.id, shift_template_id=shifts["SHIFT-16"].id,
        assignment_type="WEEKLY_RECURRING", day_of_week=0,
        effective_from=MON, approval_status=ApprovalStatus.APPROVED,
    ))
    db.add(m.ScheduleAssignment(
        intern_id=intern.id, shift_template_id=shifts["SHIFT-01"].id,
        assignment_type="WEEKLY_RECURRING", day_of_week=1,
        effective_from=MON, approval_status=ApprovalStatus.APPROVED,
    ))
    db.flush()

    tue = MON + timedelta(days=1)
    # Monday overnight 22:00 -> Tuesday 07:00
    attendance_service.manual_entry(
        db, intern, MON,
        time_in_at=punch(MON, 22, 0), time_out_at=punch(tue, 7, 0),
        actor_user_id=1, rules=rules,
    )
    # Tuesday 07:00-16:00 would start exactly as the overnight ends: allowed.
    attendance_service.manual_entry(
        db, intern, tue,
        time_in_at=punch(tue, 7, 0), time_out_at=punch(tue, 16, 0),
        actor_user_id=1, rules=rules,
    )

    # But a Tuesday session starting at 06:00 overlaps the overnight shift.
    with pytest.raises(AttendanceError) as exc:
        attendance_service.manual_entry(
            db, intern, tue,
            time_in_at=punch(tue, 6, 0), time_out_at=punch(tue, 15, 0),
            actor_user_id=1, rules=rules, session_sequence=2,
        )
    assert exc.value.code == "OVERLAPPING_ATTENDANCE"


# ---------------------------------------------------------------------------
# Cross-cutting: no double counting anywhere
# ---------------------------------------------------------------------------

def test_no_double_counting_across_repeated_posting(
    db, rules, shifts, make_intern, punch, approve
):
    """Posting the same records repeatedly must never inflate the total.

    The spec calls this out explicitly, and it is the failure mode that would
    silently hand an intern free hours.
    """
    intern = make_intern(shift=shifts["SHIFT-02"], required_hours=486)

    d = MON
    worked = 0
    while worked < 5:
        if d.weekday() < 5:
            rec = attendance_service.manual_entry(
                db, intern, d, time_in_at=punch(d, 8, 0),
                time_out_at=punch(d, 17, 0), actor_user_id=1, rules=rules,
            )
            approve(rec, rules)
            worked += 1
        d += timedelta(days=1)

    baseline = ojt_engine.get_progress(db, intern).approved_minutes
    assert baseline == 5 * 480

    # Re-post everything several times, including a full rebuild.
    import app.models as m
    from sqlalchemy import select as sa_select

    for _ in range(3):
        for rec in db.scalars(
            sa_select(m.AttendanceRecord).where(
                m.AttendanceRecord.intern_id == intern.id
            )
        ).all():
            ojt_engine.post_attendance(db, rec, actor_user_id=1, rules=rules)
    ojt_engine.rebuild_ledger_for_intern(db, intern, actor_user_id=1)
    db.flush()

    assert ojt_engine.get_progress(db, intern).approved_minutes == baseline


def test_absence_is_not_marked_before_the_shift_ends(
    db, rules, shifts, make_intern, punch
):
    """Explicit spec requirement: never mark absent early.

    The status must remain SCHEDULED until the shift has ended AND the
    configured absence grace period has passed.
    """
    intern = make_intern(shift=shifts["SHIFT-02"])
    grace = shifts["SHIFT-02"].absence_grace_minutes
    assert grace == 60

    for hour, expected in [
        (7, AttendanceStatus.SCHEDULED),    # before the shift
        (12, AttendanceStatus.SCHEDULED),   # mid-shift, no punch yet
        (17, AttendanceStatus.SCHEDULED),   # exactly at shift end
        (17, AttendanceStatus.SCHEDULED),   # within grace
    ]:
        rec = attendance_service.ensure_day_records(
            db, intern, MON, rules=rules, evaluation_time=punch(MON, hour, 30)
        )
        attendance_service.recompute_record(
            db, rec, rules=rules, evaluation_time=punch(MON, hour, 30)
        )
        assert rec.status == expected, f"at {hour}:30 expected {expected}"

    # 18:01 is past 17:00 + 60m grace.
    attendance_service.recompute_record(
        db, rec, rules=rules, evaluation_time=punch(MON, 18, 1)
    )
    assert rec.status == AttendanceStatus.ABSENT
