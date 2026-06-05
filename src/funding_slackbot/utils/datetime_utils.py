from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser

_UK_TIME_SUFFIX = " uk time"


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime_utc(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return to_utc(value)

    if isinstance(value, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.lower().endswith(_UK_TIME_SUFFIX):
            try:
                parsed = parser.parse(value[: -len(_UK_TIME_SUFFIX)].strip())
            except (ValueError, TypeError, OverflowError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Europe/London"))
            return to_utc(parsed)
        try:
            return to_utc(parser.parse(value))
        except (ValueError, TypeError, OverflowError):
            return None

    return None


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return to_utc(value).strftime("%Y-%m-%d %H:%M UTC")
