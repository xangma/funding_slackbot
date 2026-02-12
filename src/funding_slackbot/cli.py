from __future__ import annotations

import argparse
import logging
import os
import sys

from funding_slackbot.config import AppConfig, ConfigError, load_config
from funding_slackbot.filters import RuleBasedFilter
from funding_slackbot.logging_config import setup_logging
from funding_slackbot.models import Opportunity
from funding_slackbot.notifiers import SlackWebhookNotifier, render_slack_message_text
from funding_slackbot.service import FundingOpportunityService
from funding_slackbot.sources import Source, create_source
from funding_slackbot.store import SQLiteStore

logger = logging.getLogger(__name__)


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

    store = _build_store(app_config)

    if args.command == "init-db":
        store.init_db()
        logger.info("Initialized SQLite database at %s", app_config.storage.path)
        return 0

    store.init_db()
    sources = _build_sources(app_config)

    if args.command == "backfill":
        if not args.mark_seen:
            parser.error("backfill requires --mark-seen")
        return _run_backfill(store=store, sources=sources)

    filter_engine = RuleBasedFilter(app_config.filters)
    dry_run = args.command == "dry-run" or app_config.posting.dry_run

    notifier = None
    if not dry_run:
        webhook_url = os.getenv(app_config.slack.webhook_env_var, "").strip()
        if not webhook_url:
            logger.error(
                "Missing Slack webhook URL in environment variable %s",
                app_config.slack.webhook_env_var,
            )
            return 2
        notifier = SlackWebhookNotifier(webhook_url=webhook_url)

    service = FundingOpportunityService(
        sources=sources,
        filter_engine=filter_engine,
        store=store,
        notifier=notifier,
        max_posts_per_run=app_config.posting.max_posts_per_run,
        record_non_matches_as_seen=app_config.posting.record_non_matches_as_seen,
        dry_run=dry_run,
        preview_callback=_slack_dry_run_preview if dry_run else None,
    )

    stats = service.run_once()
    logger.info(
        "Run complete | processed=%d matched=%d posted=%d filtered_out=%d skipped_already_posted=%d errors=%d",
        stats.processed,
        stats.matched,
        stats.posted,
        stats.filtered_out,
        stats.skipped_already_posted,
        len(stats.errors),
    )

    return 0 if stats.ok else 1


def _build_store(app_config: AppConfig) -> SQLiteStore:
    if app_config.storage.type != "sqlite":
        raise ConfigError(f"Unsupported storage type: {app_config.storage.type}")
    return SQLiteStore(app_config.storage.path)


def _build_sources(app_config: AppConfig) -> list[Source]:
    sources: list[Source] = []
    for source_config in app_config.sources:
        source = create_source(source_config)
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
            seen = store.has_seen(opportunity.external_id)
            if seen is not None:
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


if __name__ == "__main__":
    raise SystemExit(main())
