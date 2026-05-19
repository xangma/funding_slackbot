from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from funding_slackbot.filters.base import Filter, FilterResult
from funding_slackbot.models import Opportunity
from funding_slackbot.notifiers.base import Notifier
from funding_slackbot.service import FundingOpportunityService
from funding_slackbot.sources.base import Source
from funding_slackbot.store.sqlite_store import SQLiteStore


class StaticSource(Source):
    def __init__(self, opportunities: list[Opportunity]) -> None:
        super().__init__(source_id="test_source")
        self._opportunities = opportunities

    def fetch(self) -> list[Opportunity]:
        return list(self._opportunities)


class AlwaysMatchFilter(Filter):
    def evaluate(self, opportunity: Opportunity) -> FilterResult:
        return FilterResult(matched=True, reasons=["test match"])


class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        self.calls.append(opportunity.external_id)


class FailingMarkPostedStore(SQLiteStore):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.fail_next_mark_posted = True

    def mark_posted(
        self,
        *,
        external_id: str,
        source_id: str,
        match_reason: str,
        posted_at: datetime,
    ) -> None:
        if self.fail_next_mark_posted:
            self.fail_next_mark_posted = False
            raise RuntimeError("simulated failure confirming posted_at")
        super().mark_posted(
            external_id=external_id,
            source_id=source_id,
            match_reason=match_reason,
            posted_at=posted_at,
        )


def _opportunity(source_id: str = "ukri_rss", external_id: str = "stable-id-123") -> Opportunity:
    return Opportunity(
        source_id=source_id,
        external_id=external_id,
        title="AI programme",
        url=f"https://www.ukri.org/opportunity/{external_id}",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="A matching item",
        raw={},
    )


def _service(
    *,
    source: Source,
    store: SQLiteStore,
    notifier: Notifier,
) -> FundingOpportunityService:
    return FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
    )


def test_dedupe_prevents_double_posting(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()

    opportunity = _opportunity()
    source = StaticSource([opportunity])
    notifier = RecordingNotifier()

    first_stats = _service(source=source, store=store, notifier=notifier).run_once()
    second_stats = _service(source=source, store=store, notifier=notifier).run_once()

    assert first_stats.posted == 1
    assert second_stats.posted == 0
    assert second_stats.skipped_already_posted == 1
    assert notifier.calls == ["stable-id-123"]

    seen = store.has_seen(source_id="ukri_rss", external_id="stable-id-123")
    assert seen is not None
    assert seen.posted_at is not None


def test_dedupe_is_scoped_by_source_id(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    opportunities = [
        _opportunity(source_id="source_a", external_id="shared-id"),
        _opportunity(source_id="source_b", external_id="shared-id"),
    ]
    notifier = RecordingNotifier()

    stats = _service(
        source=StaticSource(opportunities),
        store=store,
        notifier=notifier,
    ).run_once()

    assert stats.posted == 2
    assert notifier.calls == ["shared-id", "shared-id"]
    assert store.has_seen(source_id="source_a", external_id="shared-id") is not None
    assert store.has_seen(source_id="source_b", external_id="shared-id") is not None


def test_posting_state_prevents_reposting_on_confirmation_failure(tmp_path) -> None:
    store = FailingMarkPostedStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    opportunity = _opportunity()
    source = StaticSource([opportunity])
    notifier = RecordingNotifier()

    first_stats = _service(source=source, store=store, notifier=notifier).run_once()
    second_stats = _service(source=source, store=store, notifier=notifier).run_once()

    seen = store.has_seen(source_id="ukri_rss", external_id="stable-id-123")
    assert seen is not None
    assert seen.posted_at is None
    assert seen.post_status == "posting"
    assert notifier.calls == ["stable-id-123"]
    assert first_stats.posted == 1
    assert first_stats.errors
    assert second_stats.posted == 0
    assert second_stats.skipped_pending_confirmation == 1
    assert second_stats.skipped_post_in_progress == 1


def test_existing_legacy_sqlite_rows_are_migrated_without_losing_posted_state(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE opportunities (
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
            """,
            (
                "legacy-id",
                "ukri_rss",
                "2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                "Legacy posted opportunity",
                "https://example.com/legacy",
                "legacy match",
            ),
        )

    store = SQLiteStore(str(db_path))
    store.init_db()

    seen = store.has_seen(source_id="ukri_rss", external_id="legacy-id")

    assert seen is not None
    assert seen.posted_at is not None
    assert seen.post_status == "posted"
