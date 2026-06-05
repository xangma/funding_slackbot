from __future__ import annotations

import time
from urllib.parse import quote

import requests

from funding_slackbot.models import DeadlineReminder, Opportunity, OpportunityDigest
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
    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: int = 15,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        payload = build_slack_payload(opportunity, match_reason)
        response = _post_with_retries(
            self.webhook_url,
            json=payload,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )

    def post_digest(self, digest: OpportunityDigest) -> None:
        payload = build_slack_digest_payload(digest)
        response = _post_with_retries(
            self.webhook_url,
            json=payload,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )

    def post_deadline_reminders(self, reminders: list[DeadlineReminder]) -> None:
        payload = build_deadline_reminder_payload(reminders)
        response = _post_with_retries(
            self.webhook_url,
            json=payload,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )


def build_slack_payload(opportunity: Opportunity, match_reason: str) -> dict:
    source_display_raw = _source_display_name(opportunity.source_id)
    summary = _summary_text(opportunity.summary)
    blocks = [
        _section(_format_title_link(opportunity)),
        {
            "type": "section",
            "fields": _metadata_fields(
                opportunity,
                source_display=source_display_raw,
            ),
        },
        _section(f"*Matched*\n{_escape_mrkdwn(match_reason)}"),
    ]
    if summary:
        blocks.append(_section(f"*Summary*\n{summary}"))

    return {
        "text": _payload_fallback_text(opportunity, source_display_raw),
        "blocks": blocks,
    }


def build_slack_digest_payload(digest: OpportunityDigest) -> dict:
    opportunity_count = sum(len(group.items) for group in digest.groups)
    opportunity_word = "opportunity" if opportunity_count == 1 else "opportunities"
    single_item = next(_digest_items(digest), None) if opportunity_count == 1 else None
    header_lines = [f"*{_escape_mrkdwn(_digest_title(digest, opportunity_count))}*"]
    if opportunity_count != 1 and digest.introduction:
        header_lines.append(_escape_mrkdwn(digest.introduction))
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "\n".join(header_lines),
            },
        },
    ]

    if opportunity_count == 1:
        if single_item is not None:
            blocks.append(_section(_format_digest_item(single_item)))
    else:
        for group in digest.groups:
            heading_lines = [f"*{_escape_mrkdwn(group.heading)}*"]
            if group.summary:
                heading_lines.append(_escape_mrkdwn(group.summary))
            blocks.append(_section("\n".join(heading_lines)))
            for item in group.items:
                blocks.append(_section(_format_digest_item(item)))

    return {
        "text": _digest_fallback_text(
            opportunity_count,
            opportunity_word,
            single_item,
        ),
        "blocks": blocks[:50],
    }


def build_deadline_reminder_payload(reminders: list[DeadlineReminder]) -> dict:
    opportunity_word = "opportunity" if len(reminders) == 1 else "opportunities"
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Funding deadline reminders*\n"
                    f"{len(reminders)} posted {opportunity_word} closing soon."
                ),
            },
        }
    ]
    for reminder in reminders:
        opportunity = reminder.opportunity
        lines = [_format_title_link(opportunity)]
        metadata = _inline_metadata(
            [
                ("Closes", _format_optional_datetime(opportunity.closing_date)),
                ("Funder", _format_optional_text(opportunity.funder)),
                ("Type", _format_optional_text(opportunity.funding_type)),
            ]
        )
        if metadata:
            lines.append(metadata)
        if reminder.match_reason:
            lines.append(f"*Original match*\n{_escape_mrkdwn(reminder.match_reason)}")
        blocks.append(_section("\n".join(lines)))

    return {
        "text": f"{len(reminders)} funding deadline reminder(s)",
        "blocks": blocks[:50],
    }


def render_slack_message_text(opportunity: Opportunity, match_reason: str) -> str:
    payload = build_slack_payload(opportunity, match_reason)
    return _render_payload_text(payload)


def render_slack_digest_text(digest: OpportunityDigest) -> str:
    payload = build_slack_digest_payload(digest)
    return _render_payload_text(payload)


def render_deadline_reminder_text(reminders: list[DeadlineReminder]) -> str:
    payload = build_deadline_reminder_payload(reminders)
    return _render_payload_text(payload)


def _render_payload_text(payload: dict) -> str:
    lines: list[str] = []

    blocks = payload.get("blocks")
    if isinstance(blocks, list) and blocks:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            _append_payload_text(lines, block.get("text"))
            fields = block.get("fields")
            if isinstance(fields, list):
                for field in fields:
                    _append_payload_text(lines, field)
            elements = block.get("elements")
            if isinstance(elements, list):
                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    _append_payload_text(lines, element.get("text"))
    else:
        top_text = payload.get("text")
        if isinstance(top_text, str) and top_text:
            lines.append(top_text)

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


def _format_title_link(opportunity: Opportunity) -> str:
    safe_title = _escape_mrkdwn(opportunity.title).replace("|", r"\|")
    if opportunity.url:
        return f"*<{_escape_link_url(opportunity.url)}|{safe_title}>*"
    return f"*{safe_title}*"


def _format_digest_item(match) -> str:
    opportunity = match.opportunity
    lines = [_format_title_link(opportunity)]
    metadata = _inline_metadata(
        [
            ("Source", _source_display_name(opportunity.source_id)),
            ("Funder", _format_optional_text(opportunity.funder)),
            ("Type", _format_optional_text(opportunity.funding_type)),
            ("Closes", _format_optional_datetime(opportunity.closing_date)),
            ("Fund", _format_optional_text(opportunity.total_fund)),
        ]
    )
    if metadata:
        lines.append(metadata)
    lines.append(f"*Matched*\n{_escape_mrkdwn(match.match_reason)}")
    return "\n".join(lines)


def _digest_title(digest: OpportunityDigest, opportunity_count: int) -> str:
    title = digest.title or "New funding opportunities"
    if opportunity_count == 1 and title == "New funding opportunities":
        return "New funding opportunity"
    return title


def _digest_fallback_text(
    opportunity_count: int,
    opportunity_word: str,
    single_item,
) -> str:
    if opportunity_count == 1 and single_item is not None:
        return f"New funding opportunity: {single_item.opportunity.title}"
    return f"{opportunity_count} new funding {opportunity_word}"


def _digest_items(digest: OpportunityDigest):
    for group in digest.groups:
        yield from group.items


def _section(text: str) -> dict:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": _truncate(text, 2900),
        },
    }


def _metadata_fields(
    opportunity: Opportunity,
    *,
    source_display: str,
) -> list[dict]:
    fields = [
        _field("Source", source_display),
        _field("Funder", opportunity.funder),
        _field("Type", opportunity.funding_type),
        _field("Deadline", _format_optional_datetime(opportunity.closing_date)),
        _field("Opens", _format_optional_datetime(opportunity.opening_date)),
        _field("Total fund", opportunity.total_fund),
        _field("Published", _format_optional_datetime(opportunity.published_at)),
    ]
    return [field for field in fields if field is not None]


def _field(label: str, value: str | None) -> dict | None:
    value_text = _format_optional_text(value)
    if value_text == "Not specified":
        return None
    return {
        "type": "mrkdwn",
        "text": f"*{label}*\n{_escape_mrkdwn(value_text)}",
    }


def _inline_metadata(items: list[tuple[str, str | None]]) -> str:
    parts = []
    for label, value in items:
        value_text = _format_optional_text(value)
        if value_text == "Not specified":
            continue
        parts.append(f"*{label}:* {_escape_mrkdwn(value_text)}")
    return " | ".join(parts)


def _summary_text(summary: str) -> str:
    return _truncate(_escape_mrkdwn(summary), 600)


def _payload_fallback_text(opportunity: Opportunity, source_display: str) -> str:
    title = (
        f"{opportunity.title} ({opportunity.url})"
        if opportunity.url
        else opportunity.title
    )
    parts = [title, f"Source: {source_display}"]
    if opportunity.closing_date is not None:
        parts.append(f"Closes: {_format_optional_datetime(opportunity.closing_date)}")
    return _escape_mrkdwn(" | ".join(parts))


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


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


def _post_with_retries(
    webhook_url: str,
    *,
    json: dict,
    timeout_seconds: int,
    max_attempts: int,
    retry_backoff_seconds: float,
) -> requests.Response:
    retry_statuses = {408, 429, 500, 502, 503, 504}
    last_exception: requests.RequestException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(webhook_url, json=json, timeout=timeout_seconds)
        except requests.RequestException as exc:
            last_exception = exc
            if attempt == max_attempts:
                raise
            _sleep_before_retry(retry_backoff_seconds, attempt, None)
            continue

        if response.status_code not in retry_statuses or attempt == max_attempts:
            return response

        _sleep_before_retry(retry_backoff_seconds, attempt, response)

    if last_exception is not None:
        raise last_exception
    raise RuntimeError("unreachable retry state")


def _sleep_before_retry(
    retry_backoff_seconds: float,
    attempt: int,
    response: requests.Response | None,
) -> None:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            delay = max(0.0, float(retry_after))
        except ValueError:
            delay = retry_backoff_seconds * (2 ** (attempt - 1))
    else:
        delay = retry_backoff_seconds * (2 ** (attempt - 1))
    if delay > 0:
        time.sleep(delay)


def _escape_mrkdwn(value: str) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in ("\\", "*", "_", "`", "~"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _escape_link_url(value: str) -> str:
    return quote(value, safe=":/?#[]@!$'()+,;=%")
