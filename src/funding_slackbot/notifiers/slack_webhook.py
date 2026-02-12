from __future__ import annotations

import requests

from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import format_datetime

from .base import Notifier


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
    title_link = (
        f"*<{opportunity.url}|{opportunity.title}>*"
        if opportunity.url
        else f"*{opportunity.title}*"
    )
    deadline_text = _format_deadline(opportunity)

    metadata_parts = []
    if opportunity.published_at:
        metadata_parts.append(f"*Published:* {format_datetime(opportunity.published_at)}")
    metadata_parts.append(f"*Source:* {opportunity.source_id}")

    summary = opportunity.summary
    if len(summary) > 300:
        summary = f"{summary[:297]}..."

    text = f"{opportunity.title} ({opportunity.url})" if opportunity.url else opportunity.title
    text = f"{text} | Deadline: {deadline_text}"

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
                    "text": f"*Deadline:* {deadline_text}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": " | ".join(metadata_parts),
                    }
                ],
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


def _format_deadline(opportunity: Opportunity) -> str:
    if opportunity.closing_date is None:
        return "Not specified"
    return format_datetime(opportunity.closing_date)
