from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from funding_slackbot.filters import Filter
from funding_slackbot.models import Opportunity
from funding_slackbot.notifiers import Notifier
from funding_slackbot.sources import Source
from funding_slackbot.store import Store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunStats:
    processed: int = 0
    matched: int = 0
    filtered_out: int = 0
    posted: int = 0
    skipped_already_posted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class FundingOpportunityService:
    def __init__(
        self,
        *,
        sources: list[Source],
        filter_engine: Filter,
        store: Store,
        notifier: Notifier | None,
        max_posts_per_run: int,
        record_non_matches_as_seen: bool,
        dry_run: bool,
        preview_callback: Callable[[Opportunity, str], None] | None = None,
    ) -> None:
        self.sources = sources
        self.filter_engine = filter_engine
        self.store = store
        self.notifier = notifier
        self.max_posts_per_run = max_posts_per_run
        self.record_non_matches_as_seen = record_non_matches_as_seen
        self.dry_run = dry_run
        self.preview_callback = preview_callback or _default_preview

    def run_once(self) -> RunStats:
        stats = RunStats()

        for source in self.sources:
            try:
                opportunities = source.fetch()
            except Exception as exc:  # noqa: BLE001
                message = f"source {source.source_id} fetch failed: {exc}"
                logger.exception(message)
                stats.errors.append(message)
                continue

            logger.info("Source %s returned %d opportunities", source.source_id, len(opportunities))

            for opportunity in opportunities:
                if stats.posted >= self.max_posts_per_run:
                    logger.info(
                        "Reached posting limit (%d); stopping run",
                        self.max_posts_per_run,
                    )
                    return stats

                stats.processed += 1

                try:
                    seen = self.store.has_seen(opportunity.external_id)
                except Exception as exc:  # noqa: BLE001
                    message = f"failed to read dedupe state {opportunity.external_id}: {exc}"
                    logger.exception(message)
                    stats.errors.append(message)
                    continue
                if seen and seen.posted_at:
                    stats.skipped_already_posted += 1
                    continue

                filter_result = self.filter_engine.evaluate(opportunity)
                if not filter_result.matched:
                    stats.filtered_out += 1
                    if self.record_non_matches_as_seen and not self.dry_run:
                        try:
                            self.store.mark_seen(
                                external_id=opportunity.external_id,
                                source_id=opportunity.source_id,
                                title=opportunity.title,
                                url=opportunity.url,
                                match_reason=filter_result.reason_text(),
                                posted_at=None,
                            )
                        except Exception as exc:  # noqa: BLE001
                            message = (
                                f"failed to mark non-match as seen "
                                f"({opportunity.external_id}): {exc}"
                            )
                            logger.exception(message)
                            stats.errors.append(message)
                    continue

                stats.matched += 1
                reason_text = filter_result.reason_text()

                if self.dry_run:
                    self.preview_callback(opportunity, reason_text)
                    continue

                if self.notifier is None:
                    message = "notifier is required when dry_run is false"
                    logger.error(message)
                    stats.errors.append(message)
                    return stats

                try:
                    self.notifier.post(opportunity, reason_text)
                except Exception as exc:  # noqa: BLE001
                    message = f"failed to post {opportunity.external_id}: {exc}"
                    logger.exception(message)
                    stats.errors.append(message)
                    continue

                try:
                    self.store.mark_seen(
                        external_id=opportunity.external_id,
                        source_id=opportunity.source_id,
                        title=opportunity.title,
                        url=opportunity.url,
                        match_reason=reason_text,
                        posted_at=datetime.now(timezone.utc),
                    )
                except Exception as exc:  # noqa: BLE001
                    message = f"failed to mark posted {opportunity.external_id}: {exc}"
                    logger.exception(message)
                    stats.errors.append(message)
                    continue

                stats.posted += 1

        return stats


def _default_preview(opportunity: Opportunity, reason: str) -> None:
    print(f"[DRY RUN] WOULD POST: {opportunity.title}")
    print(f"  URL: {opportunity.url}")
    if opportunity.published_at:
        print(f"  Published: {opportunity.published_at.isoformat()}")
    if opportunity.closing_date:
        print(f"  Closing: {opportunity.closing_date.isoformat()}")
    print(f"  Why it matched: {reason}")
    print(f"  Source: {opportunity.source_id}")
    print("")
