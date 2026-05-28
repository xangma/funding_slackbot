from __future__ import annotations

from datetime import datetime, timezone

import pytest

from funding_slackbot.cli import _slack_dry_run_preview
from funding_slackbot.models import (
    DeadlineReminder,
    Opportunity,
    OpportunityDigest,
    OpportunityGroup,
    OpportunityMatch,
)
from funding_slackbot.notifiers.slack_webhook import (
    SlackWebhookNotifier,
    build_deadline_reminder_payload,
    build_slack_digest_payload,
    build_slack_payload,
    render_deadline_reminder_text,
    render_slack_digest_text,
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


class _DummyResponse:
    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


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


def test_payload_escapes_user_controlled_mrkdwn_and_link_url() -> None:
    payload = build_slack_payload(
        _opportunity(
            title="AI_*`pilot`",
            url="https://example.test/path?a=1&b=two|bad>tail",
            summary="Use *bold* _italics_ and `code`",
            funder="MRC_RSE",
        ),
        "keywords: AI",
    )
    rendered = render_slack_message_text(
        _opportunity(
            title="AI_*`pilot`",
            url="https://example.test/path?a=1&b=two|bad>tail",
            summary="Use *bold* _italics_ and `code`",
            funder="MRC_RSE",
        ),
        "keywords: AI",
    )

    block_text = "\n".join(
        block["text"]["text"]
        for block in payload["blocks"]
        if block.get("type") == "section"
    )
    assert r"AI\_\*\`pilot\`" in block_text
    assert "https://example.test/path?a=1%26b=two%7Cbad%3Etail" in block_text
    assert r"Use \*bold\* \_italics\_ and \`code\`" in block_text
    assert r"MRC\_RSE" in block_text
    assert r"AI\_\*\`pilot\`" in rendered


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


def test_digest_payload_keeps_real_links_and_deadlines() -> None:
    opportunity = _opportunity()
    digest = OpportunityDigest(
        title="AI and health funding",
        introduction="Two related calls.",
        groups=[
            OpportunityGroup(
                heading="AI health",
                summary="Matched calls for applied AI.",
                items=[OpportunityMatch(opportunity, "keywords: AI")],
            )
        ],
        generated_by_llm=True,
    )

    payload = build_slack_digest_payload(digest)
    rendered = render_slack_digest_text(digest)

    assert payload["text"] == "1 new funding opportunities"
    assert "Grouped by local LLM" in rendered
    assert "<https://www.ukri.org/opportunity/test|AI opportunity>" in rendered
    assert "closes 2026-03-30 17:00 UTC" in rendered


def test_deadline_reminder_payload_lists_closing_dates() -> None:
    reminder = DeadlineReminder(
        opportunity=_opportunity(),
        match_reason="keywords: AI",
        original_posted_at=datetime(2026, 1, 11, tzinfo=timezone.utc),
    )

    payload = build_deadline_reminder_payload([reminder])
    rendered = render_deadline_reminder_text([reminder])

    assert payload["text"] == "1 funding deadline reminder(s)"
    assert "*Funding deadline reminders*" in rendered
    assert "*Closes:* 2026-03-30 17:00 UTC" in rendered


def test_payload_escapes_slack_mrkdwn_control_sequences() -> None:
    payload = build_slack_payload(
        _opportunity(
            title="AI <alert|title>",
            url="https://example.com/opportunity?x=1|bad",
            summary="Summary with <!here> & <tags>",
        ),
        "matched <AI> & <!channel>",
    )
    block_text = "\n".join(
        block["text"]["text"]
        for block in payload["blocks"]
        if block["type"] == "section"
    )

    assert "<!here>" not in block_text
    assert "<!channel>" not in block_text
    assert "&lt;!here&gt;" in block_text
    assert "matched &lt;AI&gt; &amp; &lt;!channel&gt;" in block_text
    assert "x=1%7Cbad" in block_text


def test_slack_post_retries_transient_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_DummyResponse(429), _DummyResponse(200)]
    responses[0].headers["Retry-After"] = "0"
    calls: list[dict] = []

    def fake_post(*args: object, **kwargs: object) -> _DummyResponse:
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("requests.post", fake_post)

    notifier = SlackWebhookNotifier(
        webhook_url="https://hooks.slack.com/services/test",
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    notifier.post(_opportunity(), "test match")

    assert len(calls) == 2
