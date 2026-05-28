from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import ipaddress
import logging
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

from funding_slackbot.config import AppConfig, ConfigError, load_config
from funding_slackbot.filters import RuleBasedFilter
from funding_slackbot.llm import LocalLLMClient
from funding_slackbot.logging_config import setup_logging
from funding_slackbot.models import DeadlineReminder, Opportunity, OpportunityDigest
from funding_slackbot.notifiers import (
    SlackWebhookNotifier,
    render_deadline_reminder_text,
    render_slack_digest_text,
    render_slack_message_text,
)
from funding_slackbot.service import FundingOpportunityService
from funding_slackbot.sources import Source, create_source
from funding_slackbot.sources.registry import SourceRegistrationError
from funding_slackbot.store import SQLiteStore

logger = logging.getLogger(__name__)


class LockError(RuntimeError):
    """Raised when another bot process already holds the run lock."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="funding-bot",
        description="Poll funding sources and post matching opportunities to Slack.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        help="Override config log level (e.g. INFO, DEBUG)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Fetch once and post new matching opportunities")
    subparsers.add_parser("dry-run", help="Fetch once and print matching opportunities")
    subparsers.add_parser("init-db", help="Initialize SQLite schema")

    backfill = subparsers.add_parser(
        "backfill",
        help="Fetch current items and mark them seen without posting",
    )
    backfill.add_argument(
        "--mark-seen",
        action="store_true",
        help="Required safety flag for backfill operation",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        app_config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    log_level = args.log_level or app_config.log_level
    setup_logging(log_level)

    try:
        store = _build_store(app_config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    try:
        with _store_lock(app_config.storage.path):
            return _run_command(
                args=args,
                parser=parser,
                app_config=app_config,
                store=store,
            )
    except LockError as exc:
        logger.error("%s", exc)
        return 1


def _run_command(
    *,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    app_config: AppConfig,
    store: SQLiteStore,
) -> int:
    if args.command == "init-db":
        store.init_db()
        logger.info("Initialized SQLite database at %s", app_config.storage.path)
        return 0

    store.init_db()
    try:
        sources = _build_sources(app_config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "backfill":
        if not args.mark_seen:
            parser.error("backfill requires --mark-seen")
        return _run_backfill(store=store, sources=sources)

    filter_engine = RuleBasedFilter(app_config.filters)
    dry_run = args.command == "dry-run" or app_config.posting.dry_run
    llm_client = _build_llm_client(app_config)

    notifier = None
    if not dry_run:
        webhook_url = os.getenv(app_config.slack.webhook_env_var, "").strip()
        if not webhook_url:
            logger.error(
                "Missing Slack webhook URL in environment variable %s",
                app_config.slack.webhook_env_var,
            )
            return 2
        notifier = SlackWebhookNotifier(
            webhook_url=webhook_url,
            timeout_seconds=app_config.slack.timeout_seconds,
            max_attempts=app_config.slack.retry_attempts,
            retry_backoff_seconds=app_config.slack.retry_backoff_seconds,
        )

    service = FundingOpportunityService(
        sources=sources,
        filter_engine=filter_engine,
        store=store,
        notifier=notifier,
        max_posts_per_run=app_config.posting.max_posts_per_run,
        record_non_matches_as_seen=app_config.posting.record_non_matches_as_seen,
        dry_run=dry_run,
        llm_client=llm_client,
        group_opportunities_with_llm=(
            app_config.llm.enabled and app_config.llm.group_opportunities
        ),
        batch_new_opportunities=app_config.digest.batch_new_opportunities,
        digest_post_at_hour=app_config.digest.post_at_hour,
        digest_timezone=app_config.digest.timezone,
        digest_post_when_pending_count_reaches=(
            app_config.digest.post_when_pending_count_reaches
        ),
        deadline_reminders_enabled=app_config.reminders.enabled,
        deadline_reminder_days=app_config.reminders.days_before_deadline,
        max_deadline_reminders=app_config.reminders.max_reminders_per_run,
        preview_callback=_slack_dry_run_preview if dry_run else None,
        digest_preview_callback=_slack_digest_dry_run_preview if dry_run else None,
        reminder_preview_callback=_deadline_dry_run_preview if dry_run else None,
    )

    started_at = datetime.now(timezone.utc)
    stats = service.run_once()
    completed_at = datetime.now(timezone.utc)
    if args.command == "run" and not dry_run:
        _record_run(
            store=store,
            started_at=started_at,
            completed_at=completed_at,
            command=args.command,
            stats=stats,
        )
    logger.info(
        "Run complete | processed=%d matched=%d posted=%d "
        "grouped_messages=%d queued_for_digest=%d pending_digest=%d "
        "reminders_due=%d reminders_posted=%d "
        "filtered_out=%d skipped_already_posted=%d "
        "skipped_pending_digest=%d skipped_pending_confirmation=%d "
        "skipped_post_in_progress=%d errors=%d",
        stats.processed,
        stats.matched,
        stats.posted,
        stats.grouped_messages_posted,
        stats.queued_for_digest,
        stats.pending_digest,
        stats.reminders_due,
        stats.reminders_posted,
        stats.filtered_out,
        stats.skipped_already_posted,
        stats.skipped_pending_digest,
        stats.skipped_pending_confirmation,
        stats.skipped_post_in_progress,
        len(stats.errors),
    )

    return 0 if stats.ok else 1


def _build_store(app_config: AppConfig) -> SQLiteStore:
    if app_config.storage.type != "sqlite":
        raise ConfigError(f"Unsupported storage type: {app_config.storage.type}")
    return SQLiteStore(app_config.storage.path)


@contextmanager
def _store_lock(storage_path: str):
    lock_target = Path(storage_path).expanduser()
    lock_target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = lock_target.with_name(f"{lock_target.name}.lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockError(f"Another funding-bot process is using {storage_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _record_run(
    *,
    store: SQLiteStore,
    started_at: datetime,
    completed_at: datetime,
    command: str,
    stats,
) -> None:
    try:
        store.record_run(
            started_at=started_at,
            completed_at=completed_at,
            command=command,
            ok=stats.ok,
            processed=stats.processed,
            matched=stats.matched,
            filtered_out=stats.filtered_out,
            posted=stats.posted,
            grouped_messages_posted=stats.grouped_messages_posted,
            queued_for_digest=stats.queued_for_digest,
            pending_digest=stats.pending_digest,
            reminders_due=stats.reminders_due,
            reminders_posted=stats.reminders_posted,
            errors_count=len(stats.errors),
            error_summary="; ".join(stats.errors[:3]) or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record run telemetry: %s", exc)


def _build_llm_client(app_config: AppConfig) -> LocalLLMClient | None:
    if not app_config.llm.enabled:
        return None

    api_key = None
    if app_config.llm.api_key_env_var:
        api_key = os.getenv(app_config.llm.api_key_env_var, "").strip() or None
        if api_key is None:
            logger.warning(
                "LLM API key environment variable %s is configured but not set",
                app_config.llm.api_key_env_var,
            )
        elif _uses_plain_http_remote(app_config.llm.base_url):
            logger.warning(
                "LLM API key will be sent over plain HTTP to %s",
                app_config.llm.base_url,
            )

    return LocalLLMClient(
        base_url=app_config.llm.base_url,
        model=app_config.llm.model,
        timeout_seconds=app_config.llm.timeout_seconds,
        max_tokens=app_config.llm.max_tokens,
        temperature=app_config.llm.temperature,
        api_key=api_key,
        retry_attempts=app_config.llm.retry_attempts,
        retry_backoff_seconds=app_config.llm.retry_backoff_seconds,
        prompt_summary_chars=app_config.llm.prompt_summary_chars,
    )


def _uses_plain_http_remote(base_url: str) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme != "http":
        return False
    hostname = parsed.hostname
    if hostname is None or hostname == "localhost":
        return False
    try:
        return not ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return True


def _build_sources(app_config: AppConfig) -> list[Source]:
    sources: list[Source] = []
    for source_config in app_config.sources:
        try:
            source = create_source(source_config)
        except SourceRegistrationError as exc:
            raise ConfigError(str(exc)) from exc
        sources.append(source)
    return sources


def _run_backfill(*, store: SQLiteStore, sources: list[Source]) -> int:
    errors = 0
    marked = 0

    for source in sources:
        try:
            opportunities = source.fetch()
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception("backfill fetch failed for %s: %s", source.source_id, exc)
            continue

        for opportunity in opportunities:
            try:
                seen = store.has_seen(
                    source_id=opportunity.source_id,
                    external_id=opportunity.external_id,
                )
                if seen is not None:
                    continue
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.exception(
                    "failed to check seen during backfill for %s: %s",
                    opportunity.external_id,
                    exc,
                )
                continue

            try:
                store.mark_seen(
                    external_id=opportunity.external_id,
                    source_id=opportunity.source_id,
                    title=opportunity.title,
                    url=opportunity.url,
                    match_reason="backfill mark_seen",
                    posted_at=None,
                )
                marked += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.exception(
                    "failed to mark seen during backfill for %s: %s",
                    opportunity.external_id,
                    exc,
                )

    logger.info("Backfill complete | marked_seen=%d errors=%d", marked, errors)
    return 0 if errors == 0 else 1


def _slack_dry_run_preview(opportunity: Opportunity, reason: str) -> None:
    print("[DRY RUN] WOULD POST TEXT:")
    print(render_slack_message_text(opportunity, reason))
    print("")


def _slack_digest_dry_run_preview(digest: OpportunityDigest) -> None:
    print("[DRY RUN] WOULD POST GROUPED TEXT:")
    print(render_slack_digest_text(digest))
    print("")


def _deadline_dry_run_preview(reminders: list[DeadlineReminder]) -> None:
    print("[DRY RUN] WOULD POST DEADLINE REMINDER TEXT:")
    print(render_deadline_reminder_text(reminders))
    print("")


if __name__ == "__main__":
    raise SystemExit(main())
