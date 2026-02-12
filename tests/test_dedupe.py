from __future__ import annotations

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


def test_dedupe_prevents_double_posting(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite"
    store = SQLiteStore(str(db_path))
    store.init_db()

    opportunity = Opportunity(
        source_id="ukri_rss",
        external_id="stable-id-123",
        title="AI programme",
        url="https://www.ukri.org/opportunity/test-ai",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="A matching item",
        raw={},
    )

    source = StaticSource([opportunity])
    notifier = RecordingNotifier()

    first_run = FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
    )
    second_run = FundingOpportunityService(
        sources=[source],
        filter_engine=AlwaysMatchFilter(),
        store=store,
        notifier=notifier,
        max_posts_per_run=10,
        record_non_matches_as_seen=True,
        dry_run=False,
    )

    first_stats = first_run.run_once()
    second_stats = second_run.run_once()

    assert first_stats.posted == 1
    assert second_stats.posted == 0
    assert second_stats.skipped_already_posted == 1
    assert notifier.calls == ["stable-id-123"]

    seen = store.has_seen("stable-id-123")
    assert seen is not None
    assert seen.posted_at is not None
