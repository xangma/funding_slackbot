from __future__ import annotations

from datetime import datetime, timezone

from funding_slackbot.utils.datetime_utils import parse_datetime_utc


def test_parse_datetime_utc_handles_uk_time_suffix() -> None:
    assert parse_datetime_utc("17 July 2026 4:00pm UK time") == datetime(
        2026,
        7,
        17,
        15,
        0,
        tzinfo=timezone.utc,
    )

