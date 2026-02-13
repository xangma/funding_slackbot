from __future__ import annotations

from datetime import datetime, timezone

from funding_slackbot.cli import _slack_dry_run_preview
from funding_slackbot.models import Opportunity
from funding_slackbot.notifiers.slack_webhook import (
    build_slack_payload,
    render_slack_message_text,
)


def _opportunity(**overrides: object) -> Opportunity:
    base = Opportunity(
        source_id="ukri_rss",
        external_id="id-123",
        title="AI opportunity",
        url="https://www.ukri.org/opportunity/test",
        published_at=datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc),
        summary="Funding call for AI projects",
        raw={},
        closing_date=datetime(2026, 3, 30, 17, 0, tzinfo=timezone.utc),
        opening_date=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        funder="MRC",
        funding_type="Grant",
        total_fund="GBP 1000000",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_payload_includes_consistent_metadata_with_source_display_name() -> None:
    payload = build_slack_payload(_opportunity(), "keywords: AI")

    assert "Closes: 2026-03-30 17:00 UTC" in payload["text"]
    assert "Source: UKRI Funding Finder" in payload["text"]
    assert payload["blocks"][1]["text"]["text"] == "\n".join(
        [
            "*Source:* UKRI Funding Finder",
            "*Funder:* MRC",
            "*Funding Type:* Grant",
            "*Total Fund:* GBP 1000000",
            "*Opens:* 2026-01-01",
            "*Closes:* 2026-03-30 17:00 UTC",
            "*Published:* 2026-01-10 09:00 UTC",
        ]
    )


def test_payload_uses_not_specified_for_missing_metadata() -> None:
    payload = build_slack_payload(
        _opportunity(
            closing_date=None,
            opening_date=None,
            published_at=None,
            funder=None,
            funding_type=None,
            total_fund=None,
        ),
        "keywords: AI",
    )

    assert "Closes: Not specified" in payload["text"]
    assert "*Funder:* Not specified" in payload["blocks"][1]["text"]["text"]
    assert "*Funding Type:* Not specified" in payload["blocks"][1]["text"]["text"]
    assert "*Total Fund:* Not specified" in payload["blocks"][1]["text"]["text"]
    assert "*Opens:* Not specified" in payload["blocks"][1]["text"]["text"]
    assert "*Closes:* Not specified" in payload["blocks"][1]["text"]["text"]
    assert "*Published:* Not specified" in payload["blocks"][1]["text"]["text"]


def test_payload_formats_date_only_fields_without_midnight_time() -> None:
    payload = build_slack_payload(
        _opportunity(closing_date=datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc)),
        "keywords: AI",
    )

    assert "Closes: 2026-03-30" in payload["text"]
    assert "*Closes:* 2026-03-30" in payload["blocks"][1]["text"]["text"]


def test_render_slack_message_text_matches_payload_text_content() -> None:
    opportunity = _opportunity()
    rendered = render_slack_message_text(opportunity, "keywords: AI")

    expected = "\n".join(
        [
            "AI opportunity (https://www.ukri.org/opportunity/test) | Closes: 2026-03-30 17:00 UTC | Source: UKRI Funding Finder",
            "*<https://www.ukri.org/opportunity/test|AI opportunity>*",
            "*Source:* UKRI Funding Finder",
            "*Funder:* MRC",
            "*Funding Type:* Grant",
            "*Total Fund:* GBP 1000000",
            "*Opens:* 2026-01-01",
            "*Closes:* 2026-03-30 17:00 UTC",
            "*Published:* 2026-01-10 09:00 UTC",
            "*Why it matched:* keywords: AI",
            "Funding call for AI projects",
        ]
    )

    assert rendered == expected


def test_cli_dry_run_preview_prints_exact_rendered_text(capsys) -> None:
    opportunity = _opportunity()
    reason = "keywords: AI"

    _slack_dry_run_preview(opportunity, reason)

    expected = (
        "[DRY RUN] WOULD POST TEXT:\n"
        f"{render_slack_message_text(opportunity, reason)}\n\n"
    )
    assert capsys.readouterr().out == expected
