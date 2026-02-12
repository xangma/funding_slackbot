from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from funding_slackbot.utils.datetime_utils import parse_datetime_utc

from .base import SeenRecord, Store


class SQLiteStore(Store):
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunities (
                    external_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    posted_at TEXT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    match_reason TEXT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opportunities_posted_at
                ON opportunities (posted_at)
                """
            )
            connection.commit()

    def has_seen(self, external_id: str) -> SeenRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT external_id, source_id, first_seen_at, posted_at, title, url, match_reason
                FROM opportunities
                WHERE external_id = ?
                """,
                (external_id,),
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
                    match_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    title = excluded.title,
                    url = excluded.url,
                    match_reason = COALESCE(excluded.match_reason, opportunities.match_reason),
                    posted_at = COALESCE(opportunities.posted_at, excluded.posted_at)
                """,
                (
                    external_id,
                    source_id,
                    now,
                    posted_value,
                    title,
                    url,
                    match_reason,
                ),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection
