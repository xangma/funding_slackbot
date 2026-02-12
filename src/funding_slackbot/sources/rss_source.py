from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)

_BREAK_TAGS = re.compile(r"</?(?:br|p|li|div|tr|h\d|ul|ol|table)[^>]*>", re.IGNORECASE)
_HTML_TAGS = re.compile(r"<[^>]+>")
_MULTISPACE = re.compile(r"\s+")
_NEXT_DATA_SCRIPT = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


class RssSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False):
            logger.warning("Feed parsing bozo exception for %s: %s", self.url, parsed.bozo_exception)

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
        title = _normalize_whitespace(str(entry.get("title", "Untitled opportunity")))
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
        summary = _html_to_text(str(summary_html))

        extracted = _extract_optional_fields(summary)
        tags = _extract_tags(entry)
        funder = extracted["funder"]
        if not funder and tags:
            funder = ", ".join(tags)

        raw_data = _to_serializable_dict(entry)

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


class WellcomeSchemesSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        listings = _extract_wellcome_listings(response.text)
        opportunities: list[Opportunity] = []
        for listing in listings:
            opportunity = self._listing_to_opportunity(listing)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities

    def _listing_to_opportunity(self, listing: dict[str, Any]) -> Opportunity | None:
        application_status = _normalize_whitespace(
            str(listing.get("scheme_accepting_applications", ""))
        )
        if application_status.lower() != "open to applications":
            return None

        title = _normalize_whitespace(
            html_lib.unescape(str(listing.get("title", "Untitled opportunity")))
        )
        raw_url = _normalize_whitespace(str(listing.get("url", "")))
        if raw_url and not raw_url.startswith(("http://", "https://")):
            raw_url = urljoin(self.url, raw_url)
        url = canonicalize_url(raw_url)

        raw_identifier = _normalize_whitespace(str(listing.get("id", ""))) or None
        fallback_seed = url or f"{self.source_id}:{title}"
        external_id = derive_external_id(raw_identifier, fallback_seed)

        summary = _html_to_text(str(listing.get("listing_summary") or ""))
        closing_date = parse_datetime_utc(listing.get("scheme_closes_for_applications"))
        opening_date = parse_datetime_utc(listing.get("scheme_opens_for_applications"))
        total_fund = _html_to_text(str(listing.get("level_of_funding") or ""))

        return Opportunity(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=None,
            summary=summary,
            raw=_to_serializable_dict(listing),
            closing_date=closing_date,
            opening_date=opening_date,
            funder="Wellcome",
            funding_type=_normalize_whitespace(str(listing.get("frequency", ""))) or None,
            total_fund=total_fund or None,
        )


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

    for line in summary.splitlines():
        normalized_line = _normalize_whitespace(line)
        if not normalized_line:
            continue

        lowered = normalized_line.lower()
        value = normalized_line.split(":", 1)[1].strip() if ":" in normalized_line else ""

        if lowered.startswith("opening date"):
            fields["opening_date"] = parse_datetime_utc(value)
        elif lowered.startswith("closing date"):
            fields["closing_date"] = parse_datetime_utc(value)
        elif lowered.startswith("funder") or lowered.startswith("council"):
            fields["funder"] = value or fields["funder"]
        elif lowered.startswith("funding type"):
            fields["funding_type"] = value
        elif lowered.startswith("total fund"):
            fields["total_fund"] = value

    return fields


def _html_to_text(value: str) -> str:
    with_breaks = _BREAK_TAGS.sub("\n", value)
    without_tags = _HTML_TAGS.sub(" ", with_breaks)
    unescaped = html_lib.unescape(without_tags)

    cleaned_lines = []
    for line in unescaped.splitlines():
        normalized = _normalize_whitespace(line)
        if normalized:
            cleaned_lines.append(normalized)
    return "\n".join(cleaned_lines)


def _normalize_whitespace(value: str) -> str:
    return _MULTISPACE.sub(" ", value).strip()


def _to_serializable_dict(value: Any) -> dict[str, Any]:
    serialized = _to_serializable(value)
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}


def _to_serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_serializable(item) for item in value]
    if isinstance(value, time.struct_time):
        return list(value)
    return str(value)


def _extract_wellcome_listings(page_text: str) -> list[dict[str, Any]]:
    match = _NEXT_DATA_SCRIPT.search(page_text)
    if match is None:
        raise RuntimeError("Wellcome source response missing __NEXT_DATA__ payload")

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Wellcome source __NEXT_DATA__ payload is not valid JSON") from exc

    page_props = payload.get("props", {}).get("pageProps", {})
    if not page_props and isinstance(payload.get("pageProps"), dict):
        page_props = payload["pageProps"]

    listings = page_props.get("initialListings", [])
    if not isinstance(listings, list):
        return []
    return [item for item in listings if isinstance(item, dict)]


@register_source("rss")
def _build_rss_source(settings: SourceSettings) -> Source:
    return RssSource(settings)


@register_source("wellcome_schemes")
def _build_wellcome_schemes_source(settings: SourceSettings) -> Source:
    return WellcomeSchemesSource(settings)
