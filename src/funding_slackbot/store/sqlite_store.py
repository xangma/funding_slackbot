from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from funding_slackbot.utils.datetime_utils import parse_datetime_utc

from .base import PostStatus, SeenRecord, Store

_POST_STATUSES: tuple[PostStatus, ...] = (
    "seen",
    "posting",
    "posted",
    "post_failed",
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
                    post_error
                FROM opportunities
                WHERE source_id = ? AND external_id = ?
                """,
                (source_id, external_id),
            ).fetchone()

        if row is None:
            return None

        return SeenRecord(
            external_id=row["external_id"],
            source_id=row["source_id"],
            first_seen_at=parse_datetime_utc(row["first_seen_at"]) or datetime.now(timezone.utc),
            posted_at=parse_datetime_utc(row["posted_at"]),
            title=row["title"],
            url=row["url"],
            match_reason=row["match_reason"],
            post_status=_normalize_post_status(row["post_status"]),
            last_post_attempt_at=parse_datetime_utc(row["last_post_attempt_at"]),
            post_error=row["post_error"],
        )

    def mark_seen(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str | None,
        posted_at: datetime | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        posted_value = posted_at.astimezone(timezone.utc).isoformat() if posted_at else None
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
                    post_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    title = excluded.title,
                    url = excluded.url,
                    match_reason = COALESCE(excluded.match_reason, opportunities.match_reason),
                    posted_at = COALESCE(opportunities.posted_at, excluded.posted_at),
                    post_status = CASE
                        WHEN opportunities.posted_at IS NOT NULL THEN 'posted'
                        WHEN excluded.posted_at IS NOT NULL THEN 'posted'
                        WHEN opportunities.post_status = 'posting' THEN opportunities.post_status
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
                    post_error
                )
                VALUES (?, ?, ?, NULL, ?, ?, ?, 'posting', ?, NULL)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
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
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

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
            _create_indexes(connection)
            return

        _migrate_legacy_opportunities_table(connection)
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
                CHECK (post_status IN ('seen', 'posting', 'posted', 'post_failed')),
            last_post_attempt_at TEXT NULL,
            post_error TEXT NULL,
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


def _migrate_legacy_opportunities_table(connection: sqlite3.Connection) -> None:
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
    post_status = (
        "CASE "
        f"WHEN {posted_at} IS NOT NULL THEN 'posted' "
        "ELSE 'seen' "
        "END"
    )

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
            post_error
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
            NULL,
            NULL
        FROM opportunities_legacy
        """
    )


def _normalize_post_status(value: Any) -> PostStatus:
    if value in _POST_STATUSES:
        return value
    return "posted" if value == "posted" else "seen"
