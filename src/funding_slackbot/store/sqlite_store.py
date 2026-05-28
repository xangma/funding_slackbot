from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from funding_slackbot.utils.datetime_utils import parse_datetime_utc

from .base import PostStatus, ReminderStatus, SeenRecord, Store

_POST_STATUSES: tuple[PostStatus, ...] = (
    "seen",
    "pending_digest",
    "posting",
    "posted",
    "post_failed",
)

_REMINDER_STATUSES: tuple[ReminderStatus, ...] = (
    "none",
    "posting",
    "posted",
    "reminder_failed",
)


class SQLiteStore(Store):
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()

    def has_seen(self, *, source_id: str, external_id: str) -> SeenRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                FROM opportunities
                WHERE source_id = ? AND external_id = ?
                """,
                (source_id, external_id),
            ).fetchone()

        if row is None:
            return None

        return _row_to_seen_record(row)

    def mark_seen(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str | None,
        posted_at: datetime | None,
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        posted_value = _datetime_to_db(posted_at)
        post_status: PostStatus = "posted" if posted_at else "seen"

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO opportunities (
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                    ?, ?, ?, ?, ?, ?, 'none', NULL, NULL, NULL
                )
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    title = excluded.title,
                    url = excluded.url,
                    last_seen_at = excluded.last_seen_at,
                    closing_date = COALESCE(excluded.closing_date, opportunities.closing_date),
                    opening_date = COALESCE(excluded.opening_date, opportunities.opening_date),
                    funder = COALESCE(excluded.funder, opportunities.funder),
                    funding_type = COALESCE(excluded.funding_type, opportunities.funding_type),
                    total_fund = COALESCE(excluded.total_fund, opportunities.total_fund),
                    match_reason = COALESCE(excluded.match_reason, opportunities.match_reason),
                    posted_at = COALESCE(opportunities.posted_at, excluded.posted_at),
                    post_status = CASE
                        WHEN opportunities.posted_at IS NOT NULL THEN 'posted'
                        WHEN excluded.posted_at IS NOT NULL THEN 'posted'
                        WHEN opportunities.post_status IN ('posting', 'pending_digest') THEN opportunities.post_status
                        ELSE excluded.post_status
                    END
                """,
                (
                    external_id,
                    source_id,
                    now,
                    posted_value,
                    title,
                    url,
                    match_reason,
                    post_status,
                    now,
                    _datetime_to_db(closing_date),
                    _datetime_to_db(opening_date),
                    funder,
                    funding_type,
                    total_fund,
                ),
            )
            connection.commit()

    def claim_for_post(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str,
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO opportunities (
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                )
                VALUES (
                    ?, ?, ?, NULL, ?, ?, ?, 'posting', ?, NULL,
                    ?, ?, ?, ?, ?, ?, 'none', NULL, NULL, NULL
                )
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    last_seen_at = excluded.last_seen_at,
                    closing_date = COALESCE(excluded.closing_date, opportunities.closing_date),
                    opening_date = COALESCE(excluded.opening_date, opportunities.opening_date),
                    funder = COALESCE(excluded.funder, opportunities.funder),
                    funding_type = COALESCE(excluded.funding_type, opportunities.funding_type),
                    total_fund = COALESCE(excluded.total_fund, opportunities.total_fund),
                    match_reason = excluded.match_reason,
                    post_status = 'posting',
                    last_post_attempt_at = excluded.last_post_attempt_at,
                    post_error = NULL
                WHERE opportunities.posted_at IS NULL
                    AND opportunities.post_status NOT IN ('posted', 'posting')
                """,
                (
                    external_id,
                    source_id,
                    now,
                    title,
                    url,
                    match_reason,
                    now,
                    now,
                    _datetime_to_db(closing_date),
                    _datetime_to_db(opening_date),
                    funder,
                    funding_type,
                    total_fund,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def queue_for_digest(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str,
        queued_at: datetime,
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
    ) -> bool:
        queued_value = queued_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO opportunities (
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                )
                VALUES (
                    ?, ?, ?, NULL, ?, ?, ?, 'pending_digest', NULL, NULL,
                    ?, ?, ?, ?, ?, ?, 'none', NULL, NULL, NULL
                )
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    last_seen_at = excluded.last_seen_at,
                    closing_date = COALESCE(excluded.closing_date, opportunities.closing_date),
                    opening_date = COALESCE(excluded.opening_date, opportunities.opening_date),
                    funder = COALESCE(excluded.funder, opportunities.funder),
                    funding_type = COALESCE(excluded.funding_type, opportunities.funding_type),
                    total_fund = COALESCE(excluded.total_fund, opportunities.total_fund),
                    match_reason = excluded.match_reason,
                    post_status = 'pending_digest',
                    post_error = NULL
                WHERE opportunities.posted_at IS NULL
                    AND opportunities.post_status NOT IN ('posted', 'posting', 'pending_digest')
                """,
                (
                    external_id,
                    source_id,
                    queued_value,
                    title,
                    url,
                    match_reason,
                    queued_value,
                    _datetime_to_db(closing_date),
                    _datetime_to_db(opening_date),
                    funder,
                    funding_type,
                    total_fund,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_pending_digest(
        self,
        *,
        limit: int,
    ) -> list[SeenRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                FROM opportunities
                WHERE posted_at IS NULL
                    AND post_status = 'pending_digest'
                ORDER BY first_seen_at ASC, title ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [_row_to_seen_record(row) for row in rows]

    def mark_posted(
        self,
        *,
        external_id: str,
        source_id: str,
        match_reason: str,
        posted_at: datetime,
    ) -> None:
        posted_value = posted_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE opportunities
                SET posted_at = ?,
                    match_reason = ?,
                    post_status = 'posted',
                    post_error = NULL
                WHERE source_id = ? AND external_id = ?
                """,
                (posted_value, match_reason, source_id, external_id),
            )
            connection.commit()

    def mark_post_failed(
        self,
        *,
        external_id: str,
        source_id: str,
        error: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE opportunities
                SET post_status = 'post_failed',
                    post_error = ?
                WHERE source_id = ? AND external_id = ? AND posted_at IS NULL
                """,
                (error, source_id, external_id),
            )
            connection.commit()

    def list_due_deadline_reminders(
        self,
        *,
        now: datetime,
        days_before_deadline: int,
        limit: int,
    ) -> list[SeenRecord]:
        now_value = now.astimezone(timezone.utc).isoformat()
        due_before = (
            now.astimezone(timezone.utc)
            + timedelta(days=days_before_deadline)
        ).isoformat()

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    external_id,
                    source_id,
                    first_seen_at,
                    posted_at,
                    title,
                    url,
                    match_reason,
                    post_status,
                    last_post_attempt_at,
                    post_error,
                    last_seen_at,
                    closing_date,
                    opening_date,
                    funder,
                    funding_type,
                    total_fund,
                    reminder_status,
                    last_reminder_attempt_at,
                    reminder_posted_at,
                    reminder_error
                FROM opportunities
                WHERE posted_at IS NOT NULL
                    AND post_status = 'posted'
                    AND closing_date IS NOT NULL
                    AND closing_date >= ?
                    AND closing_date <= ?
                    AND reminder_status NOT IN ('posted', 'posting')
                ORDER BY closing_date ASC, title ASC
                LIMIT ?
                """,
                (now_value, due_before, limit),
            ).fetchall()

        return [_row_to_seen_record(row) for row in rows]

    def claim_deadline_reminder(
        self,
        *,
        external_id: str,
        source_id: str,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE opportunities
                SET reminder_status = 'posting',
                    last_reminder_attempt_at = ?,
                    reminder_error = NULL
                WHERE source_id = ?
                    AND external_id = ?
                    AND posted_at IS NOT NULL
                    AND post_status = 'posted'
                    AND reminder_status NOT IN ('posted', 'posting')
                """,
                (now, source_id, external_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def mark_deadline_reminder_posted(
        self,
        *,
        external_id: str,
        source_id: str,
        posted_at: datetime,
    ) -> None:
        posted_value = posted_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE opportunities
                SET reminder_status = 'posted',
                    reminder_posted_at = ?,
                    reminder_error = NULL
                WHERE source_id = ? AND external_id = ?
                """,
                (posted_value, source_id, external_id),
            )
            connection.commit()

    def mark_deadline_reminder_failed(
        self,
        *,
        external_id: str,
        source_id: str,
        error: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE opportunities
                SET reminder_status = 'reminder_failed',
                    reminder_error = ?
                WHERE source_id = ?
                    AND external_id = ?
                    AND reminder_status = 'posting'
                """,
                (error, source_id, external_id),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if not _table_exists(connection, "opportunities"):
            _create_opportunities_table(connection, "opportunities")
            _create_indexes(connection)
            return

        columns = _table_columns(connection, "opportunities")
        if _is_current_schema(columns):
            _ensure_deadline_columns(connection, columns)
            if not _post_status_accepts_pending_digest(connection):
                _rebuild_opportunities_table(connection)
            _create_indexes(connection)
            return

        _migrate_legacy_opportunities_table(connection)
        _ensure_deadline_columns(connection, _table_columns(connection, "opportunities"))
        if not _post_status_accepts_pending_digest(connection):
            _rebuild_opportunities_table(connection)
        _create_indexes(connection)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(
    connection: sqlite3.Connection, table_name: str
) -> dict[str, sqlite3.Row]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]): row for row in rows}


def _is_current_schema(columns: dict[str, sqlite3.Row]) -> bool:
    source_id = columns.get("source_id")
    external_id = columns.get("external_id")
    return (
        source_id is not None
        and external_id is not None
        and int(source_id["pk"]) > 0
        and int(external_id["pk"]) > 0
        and "post_status" in columns
        and "last_post_attempt_at" in columns
        and "post_error" in columns
    )


def _post_status_accepts_pending_digest(
    connection: sqlite3.Connection,
) -> bool:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'opportunities'
        """
    ).fetchone()
    if row is None:
        return False
    return "pending_digest" in str(row["sql"])


def _create_opportunities_table(
    connection: sqlite3.Connection, table_name: str
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            posted_at TEXT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            match_reason TEXT NULL,
            post_status TEXT NOT NULL DEFAULT 'seen'
                CHECK (post_status IN ('seen', 'pending_digest', 'posting', 'posted', 'post_failed')),
            last_post_attempt_at TEXT NULL,
            post_error TEXT NULL,
            last_seen_at TEXT NULL,
            closing_date TEXT NULL,
            opening_date TEXT NULL,
            funder TEXT NULL,
            funding_type TEXT NULL,
            total_fund TEXT NULL,
            reminder_status TEXT NOT NULL DEFAULT 'none',
            last_reminder_attempt_at TEXT NULL,
            reminder_posted_at TEXT NULL,
            reminder_error TEXT NULL,
            PRIMARY KEY (source_id, external_id)
        )
        """
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_opportunities_posted_at
        ON opportunities (posted_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_opportunities_status
        ON opportunities (post_status)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_opportunities_closing_date
        ON opportunities (closing_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_opportunities_reminder_status
        ON opportunities (reminder_status)
        """
    )


def _ensure_deadline_columns(
    connection: sqlite3.Connection,
    columns: dict[str, sqlite3.Row],
) -> None:
    column_definitions = {
        "last_seen_at": "TEXT NULL",
        "closing_date": "TEXT NULL",
        "opening_date": "TEXT NULL",
        "funder": "TEXT NULL",
        "funding_type": "TEXT NULL",
        "total_fund": "TEXT NULL",
        "reminder_status": "TEXT NOT NULL DEFAULT 'none'",
        "last_reminder_attempt_at": "TEXT NULL",
        "reminder_posted_at": "TEXT NULL",
        "reminder_error": "TEXT NULL",
    }
    for column_name, definition in column_definitions.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE opportunities ADD COLUMN {column_name} {definition}"
            )


def _migrate_legacy_opportunities_table(connection: sqlite3.Connection) -> None:
    _rebuild_opportunities_table(connection)


def _rebuild_opportunities_table(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "opportunities")
    _create_opportunities_table(connection, "opportunities_new")
    connection.execute("ALTER TABLE opportunities RENAME TO opportunities_legacy")
    connection.execute("ALTER TABLE opportunities_new RENAME TO opportunities")
    _copy_legacy_rows(connection, columns)
    connection.execute("DROP TABLE opportunities_legacy")


def _copy_legacy_rows(
    connection: sqlite3.Connection, columns: dict[str, sqlite3.Row]
) -> None:
    def legacy_expression(column: str, fallback: str) -> str:
        return column if column in columns else fallback

    source_id = legacy_expression("source_id", "'unknown'")
    external_id = legacy_expression("external_id", "lower(hex(randomblob(16)))")
    first_seen_at = legacy_expression("first_seen_at", "datetime('now')")
    posted_at = legacy_expression("posted_at", "NULL")
    title = legacy_expression("title", "''")
    url = legacy_expression("url", "''")
    match_reason = legacy_expression("match_reason", "NULL")
    post_status = _normalized_status_expression(
        columns,
        column="post_status",
        valid_statuses=_POST_STATUSES,
        fallback=(
            "CASE "
            f"WHEN {posted_at} IS NOT NULL THEN 'posted' "
            "ELSE 'seen' "
            "END"
        ),
    )
    reminder_status = _normalized_status_expression(
        columns,
        column="reminder_status",
        valid_statuses=_REMINDER_STATUSES,
        fallback="'none'",
    )
    last_post_attempt_at = legacy_expression("last_post_attempt_at", "NULL")
    post_error = legacy_expression("post_error", "NULL")
    last_seen_at = legacy_expression("last_seen_at", "NULL")
    closing_date = legacy_expression("closing_date", "NULL")
    opening_date = legacy_expression("opening_date", "NULL")
    funder = legacy_expression("funder", "NULL")
    funding_type = legacy_expression("funding_type", "NULL")
    total_fund = legacy_expression("total_fund", "NULL")
    last_reminder_attempt_at = legacy_expression(
        "last_reminder_attempt_at",
        "NULL",
    )
    reminder_posted_at = legacy_expression("reminder_posted_at", "NULL")
    reminder_error = legacy_expression("reminder_error", "NULL")

    connection.execute(
        f"""
        INSERT OR IGNORE INTO opportunities (
            source_id,
            external_id,
            first_seen_at,
            posted_at,
            title,
            url,
            match_reason,
            post_status,
            last_post_attempt_at,
            post_error,
            last_seen_at,
            closing_date,
            opening_date,
            funder,
            funding_type,
            total_fund,
            reminder_status,
            last_reminder_attempt_at,
            reminder_posted_at,
            reminder_error
        )
        SELECT
            COALESCE(NULLIF({source_id}, ''), 'unknown'),
            {external_id},
            {first_seen_at},
            {posted_at},
            COALESCE({title}, ''),
            COALESCE({url}, ''),
            {match_reason},
            {post_status},
            {last_post_attempt_at},
            {post_error},
            {last_seen_at},
            {closing_date},
            {opening_date},
            {funder},
            {funding_type},
            {total_fund},
            {reminder_status},
            {last_reminder_attempt_at},
            {reminder_posted_at},
            {reminder_error}
        FROM opportunities_legacy
        """
    )


def _normalized_status_expression(
    columns: dict[str, sqlite3.Row],
    *,
    column: str,
    valid_statuses: tuple[str, ...],
    fallback: str,
) -> str:
    if column not in columns:
        return fallback

    quoted = ", ".join(f"'{status}'" for status in valid_statuses)
    return (
        "CASE "
        f"WHEN {column} IN ({quoted}) THEN {column} "
        f"ELSE {fallback} "
        "END"
    )


def _row_to_seen_record(row: sqlite3.Row) -> SeenRecord:
    return SeenRecord(
        external_id=row["external_id"],
        source_id=row["source_id"],
        first_seen_at=parse_datetime_utc(row["first_seen_at"])
        or datetime.now(timezone.utc),
        posted_at=parse_datetime_utc(row["posted_at"]),
        title=row["title"],
        url=row["url"],
        match_reason=row["match_reason"],
        post_status=_normalize_post_status(row["post_status"]),
        last_post_attempt_at=parse_datetime_utc(row["last_post_attempt_at"]),
        post_error=row["post_error"],
        last_seen_at=parse_datetime_utc(row["last_seen_at"]),
        closing_date=parse_datetime_utc(row["closing_date"]),
        opening_date=parse_datetime_utc(row["opening_date"]),
        funder=row["funder"],
        funding_type=row["funding_type"],
        total_fund=row["total_fund"],
        reminder_status=_normalize_reminder_status(row["reminder_status"]),
        last_reminder_attempt_at=parse_datetime_utc(
            row["last_reminder_attempt_at"]
        ),
        reminder_posted_at=parse_datetime_utc(row["reminder_posted_at"]),
        reminder_error=row["reminder_error"],
    )


def _datetime_to_db(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _normalize_post_status(value: Any) -> PostStatus:
    if value in _POST_STATUSES:
        return value
    return "posted" if value == "posted" else "seen"


def _normalize_reminder_status(value: Any) -> ReminderStatus:
    if value in _REMINDER_STATUSES:
        return value
    return "none"
