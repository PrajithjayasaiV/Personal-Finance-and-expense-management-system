"""Date helpers: month keys, presets and comparable-period logic."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from utils.errors import ValidationError

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")

PRESET_LABELS = {
    "current_month": "Current month",
    "previous_month": "Previous month",
    "last_30_days": "Last 30 days",
    "last_90_days": "Last 90 days",
    "current_year": "Current year",
    "this_week": "This week",
    "last_week": "Last week",
    "all_time": "All time",
    "custom": "Custom range",
}
DASHBOARD_PRESETS = ("current_month", "previous_month", "last_30_days", "last_90_days", "current_year", "custom")


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(key: str) -> tuple[int, int]:
    match = _MONTH_RE.match(str(key or "").strip())
    if not match:
        raise ValidationError("Month must be in YYYY-MM format.")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise ValidationError("Month is out of the supported range (2000-01 to 2100-12).")
    return year, month


def month_bounds(key: str) -> tuple[date, date]:
    year, month = parse_month(key)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def shift_month(key: str, n: int) -> str:
    year, month = parse_month(key)
    index = year * 12 + (month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def add_months(d: date, n: int) -> date:
    index = d.year * 12 + (d.month - 1) + n
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(d.day, days_in_month(year, month)))


def months_range(start_key: str, end_key: str) -> list[str]:
    """Inclusive list of month keys from ``start_key`` to ``end_key``."""
    out, cur = [], start_key
    while cur <= end_key:
        out.append(cur)
        cur = shift_month(cur, 1)
    return out


def month_label(key: str, short: bool = False) -> str:
    year, month = parse_month(key)
    name = calendar.month_abbr[month] if short else calendar.month_name[month]
    return f"{name} {year}"


def start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def resolve_preset(preset: str, today: date) -> tuple[date | None, date | None]:
    """Translate a preset name into an inclusive (start, end) pair."""
    if preset == "current_month":
        return today.replace(day=1), today
    if preset == "previous_month":
        return month_bounds(shift_month(month_key(today), -1))
    if preset == "last_30_days":
        return today - timedelta(days=29), today
    if preset == "last_90_days":
        return today - timedelta(days=89), today
    if preset == "current_year":
        return date(today.year, 1, 1), today
    if preset == "this_week":
        return start_of_week(today), today
    if preset == "last_week":
        monday = start_of_week(today) - timedelta(days=7)
        return monday, monday + timedelta(days=6)
    if preset == "all_time":
        return None, None
    raise ValidationError(f"Unknown date range: {preset}")


def previous_comparable_period(start: date, end: date) -> tuple[date, date]:
    """The period a given range should be compared with.

    * Calendar-month ranges (full month, or month-to-date) compare with the
      previous month. A month-to-date range is compared with the *same number
      of days* of the previous month, so a half-finished month is never
      compared with a complete one.
    * Any other range compares with the immediately preceding window of equal
      length.
    """
    if start > end:
        raise ValidationError("Start date must not be after end date.")
    if start.day == 1 and (start.year, start.month) == (end.year, end.month):
        prev_start, prev_month_end = month_bounds(shift_month(month_key(start), -1))
        this_month_end = month_bounds(month_key(start))[1]
        if end == this_month_end:
            return prev_start, prev_month_end
        prev_end = prev_start + timedelta(days=min(end.day, prev_month_end.day) - 1)
        return prev_start, prev_end
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=length - 1), prev_end


def period_key(start: date, end: date) -> str:
    """Stable identifier for a period (used for insight/notification keys).

    Month-to-date ranges share one key for the whole month so a dismissed
    insight does not reappear the next day.
    """
    if start.day == 1 and (start.year, start.month) == (end.year, end.month):
        return month_key(start)
    return f"{start.isoformat()}_{end.isoformat()}"


def bucket_start(d: date, granularity: str) -> date:
    if granularity == "month":
        return d.replace(day=1)
    if granularity == "week":
        return start_of_week(d)
    return d


def bucket_starts(start: date, end: date, granularity: str) -> list[date]:
    """All bucket start dates that cover ``[start, end]`` (inclusive)."""
    out, cur = [], bucket_start(start, granularity)
    while cur <= end:
        out.append(cur)
        if granularity == "month":
            cur = add_months(cur, 1)
        elif granularity == "week":
            cur += timedelta(days=7)
        else:
            cur += timedelta(days=1)
    return out
