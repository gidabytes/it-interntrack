"""Timezone and shift-window arithmetic.

Rules enforced here:

* Timestamps are stored in UTC, always timezone-aware.
* Business dates, shift boundaries and reports are evaluated in the business
  timezone (Asia/Manila by default).
* A shift that crosses midnight belongs to its SCHEDULED START DATE. The
  business date never shifts just because the clock passed 00:00.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

MINUTES_PER_DAY = 24 * 60


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TIMEZONE)


def utc_now() -> datetime:
    """Current instant, timezone-aware, in UTC."""
    return datetime.now(timezone.utc)


def now_business() -> datetime:
    """Current instant rendered in the business timezone."""
    return utc_now().astimezone(business_tz())


def to_utc(dt: datetime) -> datetime:
    """Normalise any datetime to aware UTC.

    A naive datetime is interpreted as business-local time, which is what the
    API receives from clients that omit an offset.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=business_tz())
    return dt.astimezone(timezone.utc)


def to_business(dt: datetime) -> datetime:
    """Render an instant in the business timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(business_tz())


def business_date_of(dt: datetime) -> date:
    """Calendar date of an instant *in business local time*.

    This is the raw calendar date, NOT the attendance business date; the
    attendance business date is the scheduled start date of the matched shift
    (see services.schedule_resolver).
    """
    return to_business(dt).date()


def combine_business(d: date, t: time) -> datetime:
    """Build an aware business-local datetime from a date and a wall time."""
    return datetime.combine(d, t, tzinfo=business_tz())


def is_overnight(start: time, end: time) -> bool:
    """True when the shift's end wall-time falls on the following day.

    Equal start and end means a full 24h shift, which is also overnight.
    An end of exactly 00:00 is midnight of the next day -> overnight.
    """
    return end <= start


def shift_elapsed_minutes(start: time, end: time) -> int:
    """Nominal elapsed minutes of a shift, handling midnight rollover.

    07:00-16:00 -> 540.  22:00-07:00 -> 540.  15:00-00:00 -> 540.
    """
    start_m = start.hour * 60 + start.minute
    end_m = end.hour * 60 + end.minute
    delta = end_m - start_m
    if delta <= 0:
        delta += MINUTES_PER_DAY
    return delta


def shift_window(business_day: date, start: time, end: time) -> tuple[datetime, datetime]:
    """Absolute start/end instants of a shift scheduled on ``business_day``.

    The end instant rolls into the next calendar day for overnight shifts.
    Both returned values are business-local aware datetimes.
    """
    start_dt = combine_business(business_day, start)
    end_dt = start_dt + timedelta(minutes=shift_elapsed_minutes(start, end))
    return start_dt, end_dt


def minutes_between(a: datetime, b: datetime) -> int:
    """Whole minutes from ``a`` to ``b`` (negative if b precedes a)."""
    return int((to_utc(b) - to_utc(a)).total_seconds() // 60)


def overlap_minutes(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> int:
    """Minutes of intersection between two intervals; 0 when disjoint.

    Used to clip worked time to the scheduled window and to deduct only the
    breaks that actually fall inside a given span.
    """
    lo = max(to_utc(a_start), to_utc(b_start))
    hi = min(to_utc(a_end), to_utc(b_end))
    if hi <= lo:
        return 0
    return int((hi - lo).total_seconds() // 60)


def intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """True when two intervals share any time at all (touching ends do not)."""
    return overlap_minutes(a_start, a_end, b_start, b_end) > 0


def minutes_to_hours(minutes: int, places: int = 2) -> float:
    """Minutes as decimal hours, rounded for display/reporting."""
    return round(minutes / 60.0, places)


def fmt_hhmm(minutes: int) -> str:
    """Minutes as H:MM, for timesheets. Negative values keep their sign."""
    sign = "-" if minutes < 0 else ""
    minutes = abs(int(minutes))
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


def week_start(d: date) -> date:
    """Monday of the week containing ``d``."""
    return d - timedelta(days=d.weekday())


def date_range(start: date, end: date):
    """Inclusive iterator over calendar dates."""
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
