from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from funding_slackbot.filters.base import Filter, FilterResult
from funding_slackbot.models import (
    Opportunity,
    OpportunityDigest,
    OpportunityGroup,
    OpportunityMatch,
)
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


class DigestRecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.individual_calls: list[str] = []
        self.digest_calls: list[list[str]] = []

    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        self.individual_calls.append(opportunity.external_id)

    def post_digest(self, digest: OpportunityDigest) -> None:
        self.digest_calls.append(
            [
                match.opportunity.external_id
                for group in digest.groups
                for match in group.items
            ]
        )


class FakeLLMClient:
    def is_model_available(self) -> bool:
        return True

    def group_opportunities(
        self,
        matches: list[OpportunityMatch],
    ) -> OpportunityDigest:
        return OpportunityDigest(
            title="Grouped",
            introduction="Grouped by fake LLM.",
            groups=[
                OpportunityGroup(
                    heading="All matches",
                    summary="A grouped digest.",
                    items=matches,
                )
            ],
            generated_by_llm=True,
        )


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


def test_llm_grouping_posts_one_digest_and_marks_items_posted(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    opportunities = [
        _opportunity(external_id="first"),
        _opportunity(external_id="second"),
    ]
    source = StaticSource(opportunities)
    notifier = DigestRecordingNotifier()

    service = FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
        llm_client=FakeLLMClient(),  # type: ignore[arg-type]
        group_opportunities_with_llm=True,
    )

    stats = service.run_once()

    assert stats.posted == 2
    assert stats.grouped_messages_posted == 1
    assert stats.llm_grouping_used is True
    assert notifier.individual_calls == []
    assert notifier.digest_calls == [["first", "second"]]
    first_seen = store.has_seen(source_id="ukri_rss", external_id="first")
    second_seen = store.has_seen(source_id="ukri_rss", external_id="second")
    assert first_seen is not None
    assert second_seen is not None
    assert first_seen.post_status == "posted"
    assert second_seen.post_status == "posted"


def test_batched_llm_grouping_queues_until_digest_cutoff(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    opportunity = _opportunity(external_id="queued")
    source = StaticSource([opportunity])
    notifier = DigestRecordingNotifier()
    first_now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    first_stats = FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
        llm_client=FakeLLMClient(),  # type: ignore[arg-type]
        group_opportunities_with_llm=True,
        batch_new_opportunities=True,
        digest_post_at_hour=9,
        digest_timezone="UTC",
        digest_post_when_pending_count_reaches=10,
        now_provider=lambda: first_now,
    ).run_once()

    assert first_stats.queued_for_digest == 1
    assert first_stats.posted == 0
    assert first_stats.digest_not_due is True
    assert notifier.digest_calls == []
    seen = store.has_seen(source_id="ukri_rss", external_id="queued")
    assert seen is not None
    assert seen.post_status == "pending_digest"

    second_now = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
    second_stats = FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
        llm_client=FakeLLMClient(),  # type: ignore[arg-type]
        group_opportunities_with_llm=True,
        batch_new_opportunities=True,
        digest_post_at_hour=9,
        digest_timezone="UTC",
        digest_post_when_pending_count_reaches=10,
        now_provider=lambda: second_now,
    ).run_once()

    assert second_stats.skipped_pending_digest == 1
    assert second_stats.pending_digest == 1
    assert second_stats.digest_due is True
    assert second_stats.posted == 1
    assert notifier.digest_calls == [["queued"]]
    seen = store.has_seen(source_id="ukri_rss", external_id="queued")
    assert seen is not None
    assert seen.post_status == "posted"
    assert seen.posted_at == second_now


def test_batched_digest_does_not_include_old_posted_items(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    old_opportunity = _opportunity(external_id="old")
    new_opportunity = _opportunity(external_id="new")
    assert store.claim_for_post(
        external_id=old_opportunity.external_id,
        source_id=old_opportunity.source_id,
        title=old_opportunity.title,
        url=old_opportunity.url,
        match_reason="old match",
    )
    store.mark_posted(
        external_id=old_opportunity.external_id,
        source_id=old_opportunity.source_id,
        match_reason="old match",
        posted_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )
    notifier = DigestRecordingNotifier()

    stats = FundingOpportunityService(
        sources=[StaticSource([old_opportunity, new_opportunity])],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
        llm_client=FakeLLMClient(),  # type: ignore[arg-type]
        group_opportunities_with_llm=True,
        batch_new_opportunities=True,
        digest_post_at_hour=9,
        digest_timezone="UTC",
        digest_post_when_pending_count_reaches=1,
        now_provider=lambda: datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    ).run_once()

    assert stats.skipped_already_posted == 1
    assert stats.queued_for_digest == 1
    assert stats.posted == 1
    assert notifier.digest_calls == [["new"]]


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


def test_current_schema_allows_pending_digest_after_migration(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE opportunities (
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

    store = SQLiteStore(str(db_path))
    store.init_db()
    opportunity = _opportunity(external_id="pending")

    queued = store.queue_for_digest(
        external_id=opportunity.external_id,
        source_id=opportunity.source_id,
        title=opportunity.title,
        url=opportunity.url,
        match_reason="test match",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert queued is True
    seen = store.has_seen(source_id="ukri_rss", external_id="pending")
    assert seen is not None
    assert seen.post_status == "pending_digest"


def test_store_tracks_due_deadline_reminder_once(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "state.sqlite"))
    store.init_db()
    closing_date = datetime.now(timezone.utc) + timedelta(days=6)
    opportunity = _opportunity()

    claimed = store.claim_for_post(
        external_id=opportunity.external_id,
        source_id=opportunity.source_id,
        title=opportunity.title,
        url=opportunity.url,
        match_reason="test match",
        closing_date=closing_date,
    )
    assert claimed is True
    store.mark_posted(
        external_id=opportunity.external_id,
        source_id=opportunity.source_id,
        match_reason="test match",
        posted_at=datetime.now(timezone.utc),
    )

    due = store.list_due_deadline_reminders(
        now=datetime.now(timezone.utc),
        days_before_deadline=7,
        limit=10,
    )

    assert [record.external_id for record in due] == [opportunity.external_id]
    assert due[0].closing_date == closing_date
    assert store.claim_deadline_reminder(
        external_id=opportunity.external_id,
        source_id=opportunity.source_id,
    )
    assert (
        store.list_due_deadline_reminders(
            now=datetime.now(timezone.utc),
            days_before_deadline=7,
            limit=10,
        )
        == []
    )
    store.mark_deadline_reminder_posted(
        external_id=opportunity.external_id,
        source_id=opportunity.source_id,
        posted_at=datetime.now(timezone.utc),
    )

    seen = store.has_seen(
        source_id=opportunity.source_id,
        external_id=opportunity.external_id,
    )
    assert seen is not None
    assert seen.reminder_status == "posted"
