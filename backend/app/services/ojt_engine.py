"""OJT hour computation over an append-only ledger.

Why a ledger rather than a running total column
-----------------------------------------------
A stored ``total_hours`` field is a second source of truth that drifts the first
time a correction is applied out of band. Here, every creditable minute exists
as a ledger row tied to its source, and totals are always SUM(approved AND NOT
reversed). A correction never edits a row: it posts a REVERSAL of the old row
and a fresh row with the new figure, so the arithmetic that produced any past
number stays on record.

Formulas (from the spec)
------------------------
    Remaining Hours      = Required Hours - Approved Rendered Hours
    Completion Percentage= (Approved Rendered Hours / Required Hours) * 100

Completion is capped at 100% and remaining floors at zero, so an intern who
over-renders shows 100% complete rather than 104%, while the ledger still holds
every minute actually rendered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus, ForecastRisk, LedgerSourceType
from app.core.timeutil import minutes_to_hours, utc_now, week_start
from app.models.attendance import AttendanceRecord
from app.models.intern import Intern
from app.models.ojt import OJTHourLedger, OJTProgressSnapshot
from app.services.settings_service import RuleSet, load_rules

MILESTONES: list[tuple[int, str]] = [
    (25, "Initial Progress"),
    (50, "Halfway Completed"),
    (75, "Near Completion"),
    (90, "Final Stage"),
    (100, "Completed"),
]


class OJTLedgerError(Exception):
    def __init__(self, message: str, code: str = "OJT_LEDGER_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def _live_entry_for_source(
    db: Session, intern_id: int, source_type: str, source_id: int | None
) -> OJTHourLedger | None:
    """The current, non-reversed entry for a source, if one exists."""
    return db.scalar(
        select(OJTHourLedger).where(
            OJTHourLedger.intern_id == intern_id,
            OJTHourLedger.source_type == source_type,
            OJTHourLedger.source_id == source_id,
            OJTHourLedger.is_reversed.is_(False),
            OJTHourLedger.reversal_of_id.is_(None),
        )
    )


def reverse_entry(
    db: Session,
    entry: OJTHourLedger,
    *,
    actor_user_id: int | None = None,
    reason: str = "Superseded by correction",
) -> OJTHourLedger:
    """Mark an entry reversed and post the offsetting row.

    Both halves are kept: the original stays visible with ``is_reversed`` set,
    and the offsetting row records when and why.
    """
    if entry.is_reversed:
        raise OJTLedgerError("Ledger entry is already reversed.", code="ALREADY_REVERSED")

    now = utc_now()
    entry.is_reversed = True
    entry.reversed_at = now
    entry.reversed_by_id = actor_user_id

    counter = OJTHourLedger(
        intern_id=entry.intern_id,
        source_type=LedgerSourceType.REVERSAL,
        source_id=entry.id,
        business_date=entry.business_date,
        minutes_delta=-entry.minutes_delta,
        actual_minutes=-entry.actual_minutes,
        is_approved=entry.is_approved,
        approved_by_id=actor_user_id,
        approved_at=now if entry.is_approved else None,
        is_reversed=False,
        reversal_of_id=entry.id,
        description=f"Reversal of ledger #{entry.id}: {reason}",
    )
    db.add(counter)
    db.flush()
    return counter


def post_attendance(
    db: Session,
    record: AttendanceRecord,
    *,
    actor_user_id: int | None = None,
    rules: RuleSet | None = None,
) -> OJTHourLedger | None:
    """Sync the ledger to an attendance record's current creditable minutes.

    Idempotent and safe to call after every recomputation:

    * no live entry and nothing creditable -> nothing posted
    * no live entry and minutes creditable -> post one entry
    * live entry already matching          -> no-op (no double counting)
    * live entry differing                 -> reverse it, post the new figure

    ``credit_requires_approval`` decides whether the entry is created already
    approved or sits as pending hours.
    """
    rules = rules or load_rules(db)
    existing = _live_entry_for_source(
        db, record.intern_id, LedgerSourceType.ATTENDANCE, record.id
    )

    minutes = int(record.creditable_minutes or 0)
    approved = (
        record.is_approved if rules.bool_("credit_requires_approval") else True
    )

    if existing is not None:
        unchanged = (
            existing.minutes_delta == minutes
            and existing.actual_minutes == int(record.actual_net_minutes or 0)
            and existing.is_approved == approved
            and existing.business_date == record.business_date
        )
        if unchanged:
            return existing
        reverse_entry(
            db, existing,
            actor_user_id=actor_user_id,
            reason="Attendance record recomputed",
        )

    if minutes <= 0 and int(record.actual_net_minutes or 0) <= 0:
        return None

    note = (
        f"{record.shift_code or 'NO_SCHEDULE'}: "
        f"actual_net={record.actual_net_minutes}m, "
        f"base+ot={record.creditable_minutes}m"
        + (f", capped ({record.cap_reason})" if record.credit_capped else "")
    )

    entry = OJTHourLedger(
        intern_id=record.intern_id,
        source_type=LedgerSourceType.ATTENDANCE,
        source_id=record.id,
        business_date=record.business_date,
        minutes_delta=minutes,
        actual_minutes=int(record.actual_net_minutes or 0),
        is_approved=approved,
        approved_by_id=record.approved_by_id if approved else None,
        approved_at=record.approved_at if approved else None,
        description=f"Attendance {record.business_date} ({record.status})",
        computation_note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def post_adjustment(
    db: Session,
    intern_id: int,
    business_date: date,
    minutes_delta: int,
    *,
    actor_user_id: int,
    description: str,
    source_id: int | None = None,
    approved: bool = True,
) -> OJTHourLedger:
    """Post an approved manual hour adjustment."""
    if minutes_delta == 0:
        raise OJTLedgerError("Adjustment of zero minutes is meaningless.", code="ZERO_DELTA")
    entry = OJTHourLedger(
        intern_id=intern_id,
        source_type=LedgerSourceType.ADJUSTMENT,
        source_id=source_id,
        business_date=business_date,
        minutes_delta=minutes_delta,
        actual_minutes=minutes_delta,
        is_approved=approved,
        approved_by_id=actor_user_id if approved else None,
        approved_at=utc_now() if approved else None,
        description=description,
        computation_note="Manual adjustment",
    )
    db.add(entry)
    db.flush()
    return entry


def post_leave_credit(
    db: Session,
    intern_id: int,
    business_date: date,
    minutes: int,
    *,
    leave_request_id: int,
    actor_user_id: int,
    description: str = "Creditable approved leave",
) -> OJTHourLedger | None:
    """Credit approved leave, only where policy allows it."""
    if minutes <= 0:
        return None
    existing = _live_entry_for_source(
        db, intern_id, LedgerSourceType.LEAVE_CREDIT, leave_request_id
    )
    if existing is not None:
        return existing
    entry = OJTHourLedger(
        intern_id=intern_id,
        source_type=LedgerSourceType.LEAVE_CREDIT,
        source_id=leave_request_id,
        business_date=business_date,
        minutes_delta=minutes,
        actual_minutes=0,
        is_approved=True,
        approved_by_id=actor_user_id,
        approved_at=utc_now(),
        description=description,
        computation_note="Leave credited per configured policy",
    )
    db.add(entry)
    db.flush()
    return entry


def approve_ledger_for_record(
    db: Session, record: AttendanceRecord, *, actor_user_id: int
) -> OJTHourLedger | None:
    """Flip the record's pending ledger entry to approved."""
    entry = _live_entry_for_source(
        db, record.intern_id, LedgerSourceType.ATTENDANCE, record.id
    )
    if entry is None:
        return None
    if not entry.is_approved:
        entry.is_approved = True
        entry.approved_by_id = actor_user_id
        entry.approved_at = utc_now()
        db.flush()
    return entry


# ---------------------------------------------------------------------------
# Totals and progress
# ---------------------------------------------------------------------------

@dataclass
class OJTProgress:
    intern_id: int
    required_minutes: int
    approved_minutes: int
    pending_minutes: int
    actual_rendered_minutes: int

    @property
    def credited_minutes(self) -> int:
        """Approved minutes counted toward completion, capped at required."""
        return min(self.approved_minutes, self.required_minutes)

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.required_minutes - self.approved_minutes)

    @property
    def completion_percentage(self) -> float:
        if self.required_minutes <= 0:
            return 0.0
        pct = (self.approved_minutes / self.required_minutes) * 100
        return round(min(pct, 100.0), 2)

    @property
    def raw_completion_percentage(self) -> float:
        """Uncapped, so over-rendering is still visible to management."""
        if self.required_minutes <= 0:
            return 0.0
        return round((self.approved_minutes / self.required_minutes) * 100, 2)

    @property
    def is_complete(self) -> bool:
        return self.approved_minutes >= self.required_minutes

    @property
    def milestone(self) -> str | None:
        pct = self.completion_percentage
        reached = [label for threshold, label in MILESTONES if pct >= threshold]
        return reached[-1] if reached else None

    @property
    def next_milestone(self) -> tuple[int, str] | None:
        pct = self.completion_percentage
        for threshold, label in MILESTONES:
            if pct < threshold:
                return threshold, label
        return None

    def as_dict(self) -> dict:
        return {
            "intern_id": self.intern_id,
            "required_hours": minutes_to_hours(self.required_minutes),
            "approved_hours": minutes_to_hours(self.approved_minutes),
            "pending_hours": minutes_to_hours(self.pending_minutes),
            "actual_rendered_hours": minutes_to_hours(self.actual_rendered_minutes),
            "remaining_hours": minutes_to_hours(self.remaining_minutes),
            "completion_percentage": self.completion_percentage,
            "raw_completion_percentage": self.raw_completion_percentage,
            "is_complete": self.is_complete,
            "milestone": self.milestone,
            "next_milestone": (
                {"threshold": self.next_milestone[0], "label": self.next_milestone[1]}
                if self.next_milestone else None
            ),
            "required_minutes": self.required_minutes,
            "approved_minutes": self.approved_minutes,
            "remaining_minutes": self.remaining_minutes,
        }


def _live_posting_filter(intern_id: int) -> list:
    """What counts toward OJT totals.

    A posting counts when it is NOT reversed AND is not itself a reversal row.
    Both halves of a reversed pair are therefore excluded: dropping the
    original already removes its minutes, so also summing the offsetting
    -N row would subtract them twice.

    The offsetting row is kept in the table on purpose - it records when a
    correction happened, who made it and why - but it is an audit artifact,
    not a live posting.
    """
    return [
        OJTHourLedger.intern_id == intern_id,
        OJTHourLedger.is_reversed.is_(False),
        OJTHourLedger.reversal_of_id.is_(None),
    ]


def get_progress(
    db: Session, intern: Intern, *, as_of: date | None = None
) -> OJTProgress:
    """Derive the intern's OJT position entirely from the ledger."""
    base = _live_posting_filter(intern.id)
    if as_of:
        base.append(OJTHourLedger.business_date <= as_of)

    approved = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(*base, OJTHourLedger.is_approved.is_(True))
        )
    ) or 0
    pending = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(*base, OJTHourLedger.is_approved.is_(False))
        )
    ) or 0
    actual = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.actual_minutes), 0)).where(
            and_(*base)
        )
    ) or 0

    return OJTProgress(
        intern_id=intern.id,
        required_minutes=intern.required_minutes,
        approved_minutes=int(approved),
        pending_minutes=int(pending),
        actual_rendered_minutes=int(actual),
    )


def average_minutes_per_week(
    db: Session, intern: Intern, *, lookback_weeks: int, as_of: date | None = None
) -> int:
    """Approved minutes per week over the recent window.

    Weeks before the intern started are excluded, so a new intern's pace is not
    diluted by weeks they were not yet enrolled.
    """
    today = as_of or date.today()
    window_start = week_start(today) - timedelta(weeks=lookback_weeks)
    effective_start = max(window_start, intern.ojt_start_date)
    if effective_start > today:
        return 0

    total = db.scalar(
        select(func.coalesce(func.sum(OJTHourLedger.minutes_delta), 0)).where(
            and_(
                *_live_posting_filter(intern.id),
                OJTHourLedger.is_approved.is_(True),
                OJTHourLedger.business_date >= effective_start,
                OJTHourLedger.business_date <= today,
            )
        )
    ) or 0

    days = max(1, (today - effective_start).days + 1)
    weeks = max(1.0, days / 7.0)
    return int(total / weeks)


def write_snapshot(
    db: Session,
    intern: Intern,
    progress: OJTProgress,
    *,
    as_of: date | None = None,
    projected_completion_date: date | None = None,
    risk_level: str | None = None,
    avg_minutes_per_week: int = 0,
) -> OJTProgressSnapshot:
    """Persist a historical calculation record."""
    snap = OJTProgressSnapshot(
        intern_id=intern.id,
        as_of_date=as_of or date.today(),
        required_minutes=progress.required_minutes,
        approved_minutes=progress.approved_minutes,
        pending_minutes=progress.pending_minutes,
        actual_rendered_minutes=progress.actual_rendered_minutes,
        remaining_minutes=progress.remaining_minutes,
        completion_percentage=progress.completion_percentage,
        projected_completion_date=projected_completion_date,
        risk_level=risk_level,
        avg_minutes_per_week=avg_minutes_per_week,
    )
    db.add(snap)
    db.flush()
    return snap


def rebuild_ledger_for_intern(
    db: Session, intern: Intern, *, actor_user_id: int | None = None
) -> dict[str, int]:
    """Re-sync every attendance record to the ledger.

    A repair tool, not part of normal operation. Posts only differences, so
    running it on a healthy ledger changes nothing.
    """
    rules = load_rules(db)
    records = db.scalars(
        select(AttendanceRecord).where(
            AttendanceRecord.intern_id == intern.id,
            AttendanceRecord.is_deleted.is_(False),
        ).order_by(AttendanceRecord.business_date)
    ).all()

    stats = {"records": 0, "posted": 0, "unchanged": 0}
    for rec in records:
        stats["records"] += 1
        before = _live_entry_for_source(
            db, intern.id, LedgerSourceType.ATTENDANCE, rec.id
        )
        entry = post_attendance(db, rec, actor_user_id=actor_user_id, rules=rules)
        if entry is not None and (before is None or before.id != entry.id):
            stats["posted"] += 1
        else:
            stats["unchanged"] += 1
    return stats
