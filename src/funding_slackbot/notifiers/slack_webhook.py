from __future__ import annotations

from datetime import timezone

import requests

from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import format_datetime

from .base import Notifier


class SlackWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout_seconds: int = 15) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        payload = _build_payload(opportunity, match_reason)
        response = requests.post(
            self.webhook_url,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )


def _build_payload(opportunity: Opportunity, match_reason: str) -> dict:
    title_link = (
        f"*<{opportunity.url}|{opportunity.title}>*"
        if opportunity.url
        else f"*{opportunity.title}*"
    )

    metadata_parts = []
    if opportunity.published_at:
        metadata_parts.append(f"*Published:* {format_datetime(opportunity.published_at)}")
    if opportunity.closing_date:
        closing_date_utc = opportunity.closing_date.astimezone(timezone.utc)
        metadata_parts.append(f"*Closing:* {closing_date_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    metadata_parts.append(f"*Source:* {opportunity.source_id}")

    summary = opportunity.summary
    if len(summary) > 300:
        summary = f"{summary[:297]}..."

    text = f"{opportunity.title} ({opportunity.url})"

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
