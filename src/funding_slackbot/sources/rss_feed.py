from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from ._common import (
    default_headers,
    get_with_retries,
    html_to_text,
    http_options,
    to_serializable_dict,
)
from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)

DEFAULT_UKRI_FEED_URL = "https://www.ukri.org/opportunity/feed/"


class RssSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)

    def fetch(self) -> list[Opportunity]:
        headers = default_headers()
        response = get_with_retries(
            self.url,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        response.raise_for_status()

        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False):
            logger.warning(
                "Feed parsing bozo exception for %s: %s",
                self.url,
                parsed.bozo_exception,
            )

        opportunities: list[Opportunity] = []
        for entry in parsed.entries:
            opportunity = self._entry_to_opportunity(entry)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.published_at
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return opportunities

    def _entry_to_opportunity(self, entry: Any) -> Opportunity | None:
        title = " ".join(str(entry.get("title", "Untitled opportunity")).split())
        raw_link = str(entry.get("link", "")).strip()
        url = canonicalize_url(raw_link)

        raw_identifier = (
            str(entry.get("id", "")).strip()
            or str(entry.get("guid", "")).strip()
            or None
        )

        fallback_seed = url or f"{self.source_id}:{title}"
        external_id = derive_external_id(raw_identifier, fallback_seed)

        published_at = (
            parse_datetime_utc(entry.get("published"))
            or parse_datetime_utc(entry.get("updated"))
            or parse_datetime_utc(entry.get("pubDate"))
            or parse_datetime_utc(entry.get("published_parsed"))
            or parse_datetime_utc(entry.get("updated_parsed"))
        )

        summary_html = (
            entry.get("summary")
            or entry.get("description")
            or entry.get("subtitle")
            or ""
        )
        summary = html_to_text(str(summary_html))

        extracted = _extract_optional_fields(summary)
        if _needs_ukri_detail_fields(url, extracted):
            detail_fields = self._detail_fields_for_url(url)
            extracted = _merge_optional_fields(extracted, detail_fields)
        tags = _extract_tags(entry)
        funder = extracted["funder"]
        if tags and (not funder or _is_ukri_opportunity_url(url)):
            funder = ", ".join(tags)

        raw_data = to_serializable_dict(entry)

        return Opportunity(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=published_at,
            summary=summary,
            raw=raw_data,
            closing_date=extracted["closing_date"],
            opening_date=extracted["opening_date"],
            funder=funder,
            funding_type=extracted["funding_type"],
            total_fund=extracted["total_fund"],
        )

    def _detail_fields_for_url(self, url: str) -> dict[str, Any]:
        headers = default_headers()
        try:
            response = get_with_retries(
                url,
                timeout_seconds=self.timeout_seconds,
                headers=headers,
                max_attempts=self.retry_attempts,
                retry_backoff_seconds=self.retry_backoff_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to fetch detail metadata for %s: %s", url, exc)
            return {}

        return _extract_optional_fields(html_to_text(response.text))


def _extract_tags(entry: Any) -> list[str]:
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return []

    terms: list[str] = []
    for tag in tags:
        if isinstance(tag, dict):
            value = str(tag.get("term", "")).strip()
            if value:
                terms.append(value)
    return terms


def _extract_optional_fields(summary: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "opening_date": None,
        "closing_date": None,
        "funder": None,
        "funding_type": None,
        "total_fund": None,
    }

    pending_key: str | None = None
    for line in summary.splitlines():
        normalized_line = " ".join(line.split())
        if not normalized_line:
            continue

        if pending_key is not None:
            _set_optional_field(fields, pending_key, normalized_line)
            pending_key = None
            continue

        lowered = normalized_line.lower()
        has_colon = ":" in normalized_line
        value = normalized_line.split(":", 1)[1].strip() if has_colon else ""

        if lowered.startswith("opening date"):
            if value:
                _set_optional_field(fields, "opening_date", value)
            elif has_colon:
                pending_key = "opening_date"
        elif lowered.startswith("closing date"):
            if value:
                _set_optional_field(fields, "closing_date", value)
            elif has_colon:
                pending_key = "closing_date"
        elif lowered.startswith("funder") or lowered.startswith("council"):
            if value:
                _set_optional_field(fields, "funder", value)
            elif has_colon:
                pending_key = "funder"
        elif lowered.startswith("funding type"):
            if value:
                _set_optional_field(fields, "funding_type", value)
            elif has_colon:
                pending_key = "funding_type"
        elif lowered.startswith("total fund"):
            if value:
                _set_optional_field(fields, "total_fund", value)
            elif has_colon:
                pending_key = "total_fund"

    return fields


def _set_optional_field(fields: dict[str, Any], key: str, value: str) -> None:
    if key in {"opening_date", "closing_date"}:
        fields[key] = parse_datetime_utc(value)
    elif value:
        fields[key] = value


def _needs_ukri_detail_fields(url: str, fields: dict[str, Any]) -> bool:
    if not _is_ukri_opportunity_url(url):
        return False
    return fields.get("closing_date") is None


def _is_ukri_opportunity_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in {"www.ukri.org", "ukri.org"} and parsed.path.startswith(
        "/opportunity/"
    )


def _merge_optional_fields(
    fields: dict[str, Any],
    fallback_fields: dict[str, Any],
) -> dict[str, Any]:
    merged = fields.copy()
    for key, value in fallback_fields.items():
        current = merged.get(key)
        if current in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


@register_source("rss")
def _build_rss_source(settings: SourceSettings) -> Source:
    return RssSource(settings)
