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
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_payload_includes_explicit_deadline_section_and_fallback_text() -> None:
    payload = build_slack_payload(_opportunity(), "keywords: AI")

    assert "Deadline: 2026-03-30 17:00 UTC" in payload["text"]
    assert payload["blocks"][1]["text"]["text"] == "*Deadline:* 2026-03-30 17:00 UTC"
    assert "*Source:* ukri_rss" in payload["blocks"][2]["elements"][0]["text"]


def test_payload_uses_not_specified_when_deadline_missing() -> None:
    payload = build_slack_payload(_opportunity(closing_date=None), "keywords: AI")

    assert "Deadline: Not specified" in payload["text"]
    assert payload["blocks"][1]["text"]["text"] == "*Deadline:* Not specified"


def test_render_slack_message_text_matches_payload_text_content() -> None:
    opportunity = _opportunity()
    rendered = render_slack_message_text(opportunity, "keywords: AI")

    expected = "\n".join(
        [
            "AI opportunity (https://www.ukri.org/opportunity/test) | Deadline: 2026-03-30 17:00 UTC",
            "*<https://www.ukri.org/opportunity/test|AI opportunity>*",
            "*Deadline:* 2026-03-30 17:00 UTC",
            "*Published:* 2026-01-10 09:00 UTC | *Source:* ukri_rss",
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
