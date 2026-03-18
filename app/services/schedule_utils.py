from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


def app_timezone() -> ZoneInfo:
    return ZoneInfo(settings.APP_TIMEZONE)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_next_run_at(
    time_str: str,
    frequency: str = "daily",
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    reference_utc: datetime | None = None,
) -> datetime:
    local_tz = app_timezone()
    reference_local = _as_utc_aware(reference_utc).astimezone(local_tz)
    hh, mm = map(int, (time_str or "00:00").split(":"))
    frequency = str(frequency or "daily").strip().lower()

    if frequency == "weekly":
        target_weekday = day_of_week if day_of_week is not None else reference_local.weekday()
        target_weekday = max(0, min(int(target_weekday), 6))
        candidate_local = reference_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        days_ahead = (target_weekday - candidate_local.weekday()) % 7
        candidate_local += timedelta(days=days_ahead)
        if candidate_local <= reference_local:
            candidate_local += timedelta(days=7)
        return candidate_local.astimezone(timezone.utc).replace(tzinfo=None)

    if frequency == "monthly":
        target_day = day_of_month or 1
        target_day = max(1, min(int(target_day), 31))
        year, month = reference_local.year, reference_local.month
        last_day = calendar.monthrange(year, month)[1]
        candidate_local = datetime(year, month, min(target_day, last_day), hh, mm, tzinfo=local_tz)
        if candidate_local <= reference_local:
            month += 1
            if month > 12:
                month = 1
                year += 1
            last_day = calendar.monthrange(year, month)[1]
            candidate_local = datetime(year, month, min(target_day, last_day), hh, mm, tzinfo=local_tz)
        return candidate_local.astimezone(timezone.utc).replace(tzinfo=None)

    candidate_local = reference_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate_local <= reference_local:
        candidate_local += timedelta(days=1)
    return candidate_local.astimezone(timezone.utc).replace(tzinfo=None)
