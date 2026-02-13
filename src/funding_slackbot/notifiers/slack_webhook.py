from __future__ import annotations

import requests

from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import format_datetime, to_utc

from .base import Notifier

_SOURCE_DISPLAY_NAMES = {
    "ukri_rss": "UKRI Funding Finder",
    "wellcome_schemes": "Wellcome Schemes",
    "leverhulme_listings": "Leverhulme Trust Listings",
    "innovation_funding_search": "Innovation Funding Search",
    "portsmouth_jobs": "University of Portsmouth Jobs",
}


class SlackWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout_seconds: int = 15) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        payload = build_slack_payload(opportunity, match_reason)
        response = requests.post(
            self.webhook_url,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )


def build_slack_payload(opportunity: Opportunity, match_reason: str) -> dict:
    source_display = _source_display_name(opportunity.source_id)
    title_link = (
        f"*<{opportunity.url}|{opportunity.title}>*"
        if opportunity.url
        else f"*{opportunity.title}*"
    )
    closes_text = _format_optional_datetime(opportunity.closing_date)
    metadata_text = "\n".join(
        [
            f"*Source:* {source_display}",
            f"*Funder:* {_format_optional_text(opportunity.funder)}",
            f"*Funding Type:* {_format_optional_text(opportunity.funding_type)}",
            f"*Total Fund:* {_format_optional_text(opportunity.total_fund)}",
            f"*Opens:* {_format_optional_datetime(opportunity.opening_date)}",
            f"*Closes:* {closes_text}",
            f"*Published:* {_format_optional_datetime(opportunity.published_at)}",
        ]
    )

    summary = opportunity.summary
    if len(summary) > 300:
        summary = f"{summary[:297]}..."

    text = f"{opportunity.title} ({opportunity.url})" if opportunity.url else opportunity.title
    text = f"{text} | Closes: {closes_text} | Source: {source_display}"

    return {
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": title_link,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": metadata_text,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Why it matched:* {match_reason}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": summary or "(no summary provided)",
                },
            },
        ],
    }


def render_slack_message_text(opportunity: Opportunity, match_reason: str) -> str:
    payload = build_slack_payload(opportunity, match_reason)
    lines: list[str] = []

    top_text = payload.get("text")
    if isinstance(top_text, str) and top_text:
        lines.append(top_text)

    blocks = payload.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            _append_payload_text(lines, block.get("text"))
            elements = block.get("elements")
            if isinstance(elements, list):
                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    _append_payload_text(lines, element.get("text"))

    return "\n".join(lines)


def _append_payload_text(lines: list[str], payload_value: object) -> None:
    if isinstance(payload_value, str) and payload_value:
        lines.append(payload_value)
        return
    if isinstance(payload_value, dict):
        text = payload_value.get("text")
        if isinstance(text, str) and text:
            lines.append(text)


# Backward-compatible helper for existing tests/imports.
def _build_payload(opportunity: Opportunity, match_reason: str) -> dict:
    return build_slack_payload(opportunity, match_reason)


def _format_optional_text(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or "Not specified"


def _format_optional_datetime(value) -> str:
    if value is None:
        return "Not specified"
    value_utc = to_utc(value)
    if (
        value_utc.hour == 0
        and value_utc.minute == 0
        and value_utc.second == 0
        and value_utc.microsecond == 0
    ):
        return value_utc.strftime("%Y-%m-%d")
    return format_datetime(value_utc)


def _source_display_name(source_id: str) -> str:
    return _SOURCE_DISPLAY_NAMES.get(source_id, source_id)
