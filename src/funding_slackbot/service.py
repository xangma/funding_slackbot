from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from funding_slackbot.filters import Filter
from funding_slackbot.llm import LLMError, LocalLLMClient, build_simple_digest
from funding_slackbot.models import (
    DeadlineReminder,
    Opportunity,
    OpportunityDigest,
    OpportunityMatch,
)
from funding_slackbot.notifiers import Notifier
from funding_slackbot.sources import Source
from funding_slackbot.store import SeenRecord, Store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunStats:
    processed: int = 0
    matched: int = 0
    filtered_out: int = 0
    posted: int = 0
    skipped_already_posted: int = 0
    skipped_pending_confirmation: int = 0
    skipped_post_in_progress: int = 0
    grouped_messages_posted: int = 0
    queued_for_digest: int = 0
    skipped_pending_digest: int = 0
    pending_digest: int = 0
    digest_due: bool = False
    digest_not_due: bool = False
    llm_grouping_used: bool = False
    llm_grouping_failed: bool = False
    reminders_due: int = 0
    reminders_posted: int = 0
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
        llm_client: LocalLLMClient | None = None,
        group_opportunities_with_llm: bool = False,
        batch_new_opportunities: bool = False,
        digest_post_at_hour: int = 9,
        digest_timezone: str = "Europe/London",
        digest_post_when_pending_count_reaches: int = 10,
        deadline_reminders_enabled: bool = False,
        deadline_reminder_days: int = 7,
        max_deadline_reminders: int = 10,
        preview_callback: Callable[[Opportunity, str], None] | None = None,
        digest_preview_callback: Callable[[OpportunityDigest], None] | None = None,
        reminder_preview_callback: Callable[[list[DeadlineReminder]], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.sources = sources
        self.filter_engine = filter_engine
        self.store = store
        self.notifier = notifier
        self.max_posts_per_run = max_posts_per_run
        self.record_non_matches_as_seen = record_non_matches_as_seen
        self.dry_run = dry_run
        self.llm_client = llm_client
        self.group_opportunities_with_llm = group_opportunities_with_llm
        self.batch_new_opportunities = batch_new_opportunities
        self.digest_post_at_hour = digest_post_at_hour
        self.digest_timezone = ZoneInfo(digest_timezone)
        self.digest_post_when_pending_count_reaches = (
            digest_post_when_pending_count_reaches
        )
        self.deadline_reminders_enabled = deadline_reminders_enabled
        self.deadline_reminder_days = deadline_reminder_days
        self.max_deadline_reminders = max_deadline_reminders
        self.preview_callback = preview_callback or _default_preview
        self.digest_preview_callback = digest_preview_callback or _default_digest_preview
        self.reminder_preview_callback = (
            reminder_preview_callback or _default_reminder_preview
        )
        self.now_provider = now_provider or _utcnow

    def run_once(self) -> RunStats:
        stats = RunStats()
        pending_group: list[OpportunityMatch] = []
        grouping_enabled = self._llm_grouping_enabled()
        batching_enabled = self._digest_batching_enabled()

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
                if (
                    grouping_enabled
                    and not batching_enabled
                    and len(pending_group) >= self.max_posts_per_run
                ):
                    logger.info(
                        "Reached grouped posting limit (%d); stopping fetch",
                        self.max_posts_per_run,
                    )
                    break

                if not grouping_enabled and stats.posted >= self.max_posts_per_run:
                    logger.info(
                        "Reached posting limit (%d); stopping run",
                        self.max_posts_per_run,
                    )
                    self._post_deadline_reminders(stats)
                    return stats

                stats.processed += 1

                try:
                    seen = self.store.has_seen(
                        source_id=opportunity.source_id,
                        external_id=opportunity.external_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    message = f"failed to read dedupe state {opportunity.external_id}: {exc}"
                    logger.exception(message)
                    stats.errors.append(message)
                    continue
                if seen and (seen.posted_at or seen.post_status == "posted"):
                    stats.skipped_already_posted += 1
                    self._refresh_seen_metadata(opportunity)
                    continue
                if seen and seen.post_status == "posting":
                    stats.skipped_pending_confirmation += 1
                    stats.skipped_post_in_progress += 1
                    continue
                if seen and seen.post_status == "pending_digest":
                    stats.skipped_pending_digest += 1
                    self._refresh_seen_metadata(opportunity)
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
                                **_opportunity_metadata(opportunity),
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
                    if grouping_enabled:
                        pending_group.append(
                            OpportunityMatch(opportunity, reason_text)
                        )
                    else:
                        self.preview_callback(opportunity, reason_text)
                    continue

                if batching_enabled:
                    self._queue_for_digest(opportunity, reason_text, stats)
                    continue

                if grouping_enabled:
                    pending_group.append(OpportunityMatch(opportunity, reason_text))
                    continue

                if self.notifier is None:
                    message = "notifier is required when dry_run is false"
                    logger.error(message)
                    stats.errors.append(message)
                    return stats

                self._post_one(opportunity, reason_text, stats)

            if (
                grouping_enabled
                and not batching_enabled
                and len(pending_group) >= self.max_posts_per_run
            ):
                break

        if batching_enabled:
            if self.dry_run:
                if pending_group:
                    self.digest_preview_callback(
                        self._build_digest(pending_group, stats)
                    )
                self._preview_pending_digest(stats)
            else:
                self._post_due_pending_digest(stats)
        elif grouping_enabled and pending_group:
            if self.dry_run:
                self.digest_preview_callback(self._build_digest(pending_group, stats))
            else:
                self._post_grouped(pending_group, stats)

        self._post_deadline_reminders(stats)

        return stats

    def _llm_grouping_enabled(self) -> bool:
        return self.group_opportunities_with_llm and self.llm_client is not None

    def _digest_batching_enabled(self) -> bool:
        return self._llm_grouping_enabled() and self.batch_new_opportunities

    def _queue_for_digest(
        self,
        opportunity: Opportunity,
        reason_text: str,
        stats: RunStats,
    ) -> None:
        try:
            queued = self.store.queue_for_digest(
                external_id=opportunity.external_id,
                source_id=opportunity.source_id,
                title=opportunity.title,
                url=opportunity.url,
                match_reason=reason_text,
                queued_at=self._now(),
                **_opportunity_metadata(opportunity),
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to queue digest item {opportunity.external_id}: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            return

        if queued:
            stats.queued_for_digest += 1
        else:
            stats.skipped_pending_confirmation += 1

    def _post_due_pending_digest(self, stats: RunStats) -> None:
        try:
            records = self.store.list_pending_digest(
                limit=max(
                    self.max_posts_per_run,
                    self.digest_post_when_pending_count_reaches,
                )
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to read pending digest items: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            return

        stats.pending_digest = len(records)
        if not records:
            return

        if not self._pending_digest_due(records):
            stats.digest_not_due = True
            logger.info(
                "Digest has %d pending item(s), not due yet",
                len(records),
            )
            return

        stats.digest_due = True
        matches = [
            _record_to_match(record)
            for record in records[: self.max_posts_per_run]
        ]
        self._post_grouped(matches, stats)

    def _preview_pending_digest(self, stats: RunStats) -> None:
        try:
            records = self.store.list_pending_digest(
                limit=max(
                    self.max_posts_per_run,
                    self.digest_post_when_pending_count_reaches,
                )
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to read pending digest items: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            return

        stats.pending_digest = len(records)
        if records and self._pending_digest_due(records):
            stats.digest_due = True
            matches = [
                _record_to_match(record)
                for record in records[: self.max_posts_per_run]
            ]
            self.digest_preview_callback(self._build_digest(matches, stats))
        elif records:
            stats.digest_not_due = True

    def _post_one(
        self,
        opportunity: Opportunity,
        reason_text: str,
        stats: RunStats,
    ) -> None:
        if self.notifier is None:
            message = "notifier is required when dry_run is false"
            logger.error(message)
            stats.errors.append(message)
            return

        try:
            claimed = self.store.claim_for_post(
                external_id=opportunity.external_id,
                source_id=opportunity.source_id,
                title=opportunity.title,
                url=opportunity.url,
                match_reason=reason_text,
                **_opportunity_metadata(opportunity),
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to reserve post {opportunity.external_id}: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            return

        if not claimed:
            stats.skipped_pending_confirmation += 1
            stats.skipped_post_in_progress += 1
            return

        try:
            self.notifier.post(opportunity, reason_text)
        except Exception as exc:  # noqa: BLE001
            self._record_post_failure(opportunity, exc, stats)
            return

        stats.posted += 1
        self._mark_posted(opportunity, reason_text, stats)

    def _post_grouped(
        self,
        matches: list[OpportunityMatch],
        stats: RunStats,
    ) -> None:
        if self.notifier is None:
            message = "notifier is required when dry_run is false"
            logger.error(message)
            stats.errors.append(message)
            return

        claimed: list[OpportunityMatch] = []
        for match in matches:
            opportunity = match.opportunity
            try:
                did_claim = self.store.claim_for_post(
                    external_id=opportunity.external_id,
                    source_id=opportunity.source_id,
                    title=opportunity.title,
                    url=opportunity.url,
                    match_reason=match.match_reason,
                    **_opportunity_metadata(opportunity),
                )
            except Exception as exc:  # noqa: BLE001
                message = f"failed to reserve post {opportunity.external_id}: {exc}"
                logger.exception(message)
                stats.errors.append(message)
                continue
            if did_claim:
                claimed.append(match)
            else:
                stats.skipped_pending_confirmation += 1
                stats.skipped_post_in_progress += 1

        if not claimed:
            return

        digest = self._build_digest(claimed, stats)

        try:
            self.notifier.post_digest(digest)
        except Exception as exc:  # noqa: BLE001
            for match in claimed:
                self._record_post_failure(match.opportunity, exc, stats)
            return

        stats.grouped_messages_posted += 1
        stats.posted += len(claimed)
        posted_at = self._now()
        for match in claimed:
            self._mark_posted(
                match.opportunity,
                match.match_reason,
                stats,
                posted_at=posted_at,
            )

    def _record_post_failure(
        self,
        opportunity: Opportunity,
        exc: Exception,
        stats: RunStats,
    ) -> None:
        message = f"failed to post {opportunity.external_id}: {exc}"
        logger.exception(message)
        stats.errors.append(message)
        try:
            self.store.mark_post_failed(
                external_id=opportunity.external_id,
                source_id=opportunity.source_id,
                error=str(exc),
            )
        except Exception as mark_exc:  # noqa: BLE001
            mark_message = (
                f"failed to record post failure {opportunity.external_id}: {mark_exc}"
            )
            logger.exception(mark_message)
            stats.errors.append(mark_message)

    def _mark_posted(
        self,
        opportunity: Opportunity,
        reason_text: str,
        stats: RunStats,
        *,
        posted_at: datetime | None = None,
    ) -> None:
        try:
            self.store.mark_posted(
                external_id=opportunity.external_id,
                source_id=opportunity.source_id,
                match_reason=reason_text,
                posted_at=posted_at or self._now(),
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to mark posted {opportunity.external_id}: {exc}"
            logger.exception(message)
            stats.errors.append(message)

    def _refresh_seen_metadata(self, opportunity: Opportunity) -> None:
        if self.dry_run:
            return
        try:
            self.store.mark_seen(
                external_id=opportunity.external_id,
                source_id=opportunity.source_id,
                title=opportunity.title,
                url=opportunity.url,
                match_reason=None,
                posted_at=None,
                **_opportunity_metadata(opportunity),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to refresh metadata for %s: %s", opportunity.external_id, exc)

    def _post_deadline_reminders(self, stats: RunStats) -> None:
        if not self.deadline_reminders_enabled:
            return

        now = self._now()
        try:
            candidates = self.store.list_due_deadline_reminders(
                now=now,
                days_before_deadline=self.deadline_reminder_days,
                limit=self.max_deadline_reminders,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"failed to read deadline reminders: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            return

        stats.reminders_due = len(candidates)
        if not candidates:
            return

        reminders = [_record_to_reminder(record) for record in candidates]
        if self.dry_run:
            self.reminder_preview_callback(reminders)
            return

        if self.notifier is None:
            message = "notifier is required when dry_run is false"
            logger.error(message)
            stats.errors.append(message)
            return

        claimed_records: list[SeenRecord] = []
        for record in candidates:
            try:
                claimed = self.store.claim_deadline_reminder(
                    external_id=record.external_id,
                    source_id=record.source_id,
                )
            except Exception as exc:  # noqa: BLE001
                message = f"failed to reserve reminder {record.external_id}: {exc}"
                logger.exception(message)
                stats.errors.append(message)
                continue
            if claimed:
                claimed_records.append(record)

        if not claimed_records:
            return

        claimed_reminders = [_record_to_reminder(record) for record in claimed_records]
        try:
            self.notifier.post_deadline_reminders(claimed_reminders)
        except Exception as exc:  # noqa: BLE001
            message = f"failed to post deadline reminders: {exc}"
            logger.exception(message)
            stats.errors.append(message)
            for record in claimed_records:
                try:
                    self.store.mark_deadline_reminder_failed(
                        external_id=record.external_id,
                        source_id=record.source_id,
                        error=str(exc),
                    )
                except Exception as mark_exc:  # noqa: BLE001
                    mark_message = (
                        f"failed to record reminder failure "
                        f"{record.external_id}: {mark_exc}"
                    )
                    logger.exception(mark_message)
                    stats.errors.append(mark_message)
            return

        posted_at = self._now()
        stats.reminders_posted = len(claimed_records)
        for record in claimed_records:
            try:
                self.store.mark_deadline_reminder_posted(
                    external_id=record.external_id,
                    source_id=record.source_id,
                    posted_at=posted_at,
                )
            except Exception as exc:  # noqa: BLE001
                message = f"failed to mark reminder posted {record.external_id}: {exc}"
                logger.exception(message)
                stats.errors.append(message)

    def _build_digest(
        self,
        matches: list[OpportunityMatch],
        stats: RunStats,
    ) -> OpportunityDigest:
        try:
            if self.llm_client is None or not self.llm_client.is_model_available():
                raise LLMError("configured local LLM model is not available")
            digest = self.llm_client.group_opportunities(matches)
            stats.llm_grouping_used = True
            return digest
        except LLMError as exc:
            logger.warning("Falling back to metadata grouping: %s", exc)
            stats.llm_grouping_failed = True
            return build_simple_digest(matches, generated_by_llm=False)

    def _pending_digest_due(self, records: list[SeenRecord]) -> bool:
        if len(records) >= self.digest_post_when_pending_count_reaches:
            return True

        now_local = self._now().astimezone(self.digest_timezone)
        cutoff = now_local.replace(
            hour=self.digest_post_at_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now_local < cutoff:
            return False

        return any(
            record.first_seen_at.astimezone(self.digest_timezone) <= cutoff
            for record in records
        )

    def _now(self) -> datetime:
        return self.now_provider().astimezone(timezone.utc)


def _opportunity_metadata(opportunity: Opportunity) -> dict[str, object]:
    return {
        "closing_date": opportunity.closing_date,
        "opening_date": opportunity.opening_date,
        "funder": opportunity.funder,
        "funding_type": opportunity.funding_type,
        "total_fund": opportunity.total_fund,
    }


def _record_to_reminder(record: SeenRecord) -> DeadlineReminder:
    opportunity = Opportunity(
        source_id=record.source_id,
        external_id=record.external_id,
        title=record.title,
        url=record.url,
        published_at=None,
        summary="",
        raw={},
        closing_date=record.closing_date,
        opening_date=record.opening_date,
        funder=record.funder,
        funding_type=record.funding_type,
        total_fund=record.total_fund,
    )
    return DeadlineReminder(
        opportunity=opportunity,
        match_reason=record.match_reason,
        original_posted_at=record.posted_at,
    )


def _record_to_match(record: SeenRecord) -> OpportunityMatch:
    opportunity = Opportunity(
        source_id=record.source_id,
        external_id=record.external_id,
        title=record.title,
        url=record.url,
        published_at=None,
        summary="",
        raw={},
        closing_date=record.closing_date,
        opening_date=record.opening_date,
        funder=record.funder,
        funding_type=record.funding_type,
        total_fund=record.total_fund,
    )
    return OpportunityMatch(
        opportunity=opportunity,
        match_reason=record.match_reason or "Queued funding match.",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _default_digest_preview(digest: OpportunityDigest) -> None:
    opportunity_count = sum(len(group.items) for group in digest.groups)
    print(f"[DRY RUN] WOULD POST GROUPED DIGEST: {opportunity_count} opportunities")
    for group in digest.groups:
        print(f"  {group.heading}")
        for match in group.items:
            print(f"  - {match.opportunity.title}")
    print("")


def _default_reminder_preview(reminders: list[DeadlineReminder]) -> None:
    print(f"[DRY RUN] WOULD POST DEADLINE REMINDERS: {len(reminders)} opportunities")
    for reminder in reminders:
        closing = reminder.opportunity.closing_date
        closing_text = closing.isoformat() if closing else "unknown"
        print(f"  - {reminder.opportunity.title} | closes {closing_text}")
    print("")
