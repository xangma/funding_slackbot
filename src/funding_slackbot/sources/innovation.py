from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin

import feedparser

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from ._common import (
    default_headers,
    get_with_retries,
    html_to_text,
    http_options,
    normalize_whitespace,
    to_serializable_dict,
)
from .base import Source
from .registry import register_source
from .rss_feed import DEFAULT_UKRI_FEED_URL

logger = logging.getLogger(__name__)

_COMPETITION_CARD = re.compile(
    r"<li>\s*<h2[^>]*>.*?<a[^>]+href=[\"'](?P<href>/competition/[^\"']+)[\"'][^>]*>"
    r"(?P<title>.*?)</a>.*?<div class=\"wysiwyg-styles[^>]*>(?P<summary>.*?)</div>"
    r".*?<dl class=\"date-definition-list[^>]*>(?P<dates>.*?)</dl>",
    re.IGNORECASE | re.DOTALL,
)
_DATE_PAIR = re.compile(
    r"<dt>\s*(?P<label>Opened|Opens|Closes)\s*:\s*</dt>\s*<dd[^>]*>(?P<value>.*?)</dd>",
    re.IGNORECASE | re.DOTALL,
)
_COMPETITION_ID = re.compile(r"/competition/(?P<id>\d+)/")
_TITLE_CLEAN = re.compile(r"[^a-z0-9]+")
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


class InnovationFundingSearchSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)
        raw_ukri_url = str(
            settings.options.get("ukri_feed_url", DEFAULT_UKRI_FEED_URL)
        ).strip()
        self.ukri_feed_url = raw_ukri_url or DEFAULT_UKRI_FEED_URL

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

        cards = _extract_innovation_competition_cards(response.text)
        ukri_title_keys = self._fetch_ukri_title_keys()

        opportunities: list[Opportunity] = []
        for card in cards:
            title_key = _normalize_competition_title(card["title"])
            if title_key and _matches_ukri_title(title_key, ukri_title_keys):
                continue

            raw_url = urljoin(self.url, card["url"])
            url = canonicalize_url(raw_url)
            competition_id = _extract_competition_id(card["url"])
            external_id = (
                f"innovation-competition:{competition_id}"
                if competition_id
                else derive_external_id(None, url)
            )

            closing_date = parse_datetime_utc(card.get("closes"))
            opening_date = parse_datetime_utc(card.get("opens"))
            summary = html_to_text(card.get("summary", ""))

            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=card["title"],
                    url=url,
                    published_at=opening_date,
                    summary=summary,
                    raw=to_serializable_dict(card),
                    closing_date=closing_date,
                    opening_date=opening_date,
                    funder="Innovate UK",
                    funding_type="Competition",
                    total_fund=None,
                )
            )

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _fetch_ukri_title_keys(self) -> list[str]:
        headers = default_headers()
        try:
            response = get_with_retries(
                self.ukri_feed_url,
                timeout_seconds=self.timeout_seconds,
                headers=headers,
                max_attempts=self.retry_attempts,
                retry_backoff_seconds=self.retry_backoff_seconds,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch UKRI feed for dedupe: %s", exc)
            return []

        parsed = feedparser.parse(response.content)
        title_keys: list[str] = []
        for entry in parsed.entries:
            title = _normalize_competition_title(str(entry.get("title", "")))
            if title:
                title_keys.append(title)
        return title_keys


def _extract_innovation_competition_cards(page_text: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for match in _COMPETITION_CARD.finditer(page_text):
        dates = {
            item.group("label").lower(): html_to_text(item.group("value"))
            for item in _DATE_PAIR.finditer(match.group("dates"))
        }
        cards.append(
            {
                "url": normalize_whitespace(match.group("href")),
                "title": html_to_text(match.group("title")),
                "summary": match.group("summary"),
                "opens": dates.get("opens") or dates.get("opened") or "",
                "closes": dates.get("closes") or "",
            }
        )
    return cards


def _extract_competition_id(url_value: str) -> str | None:
    match = _COMPETITION_ID.search(url_value)
    if match is None:
        return None
    return match.group("id")


def _normalize_competition_title(value: str) -> str:
    normalized = html_to_text(value).lower()
    normalized = normalized.replace("eoi", "expression of interest")
    for word, digit in _NUMBER_WORDS.items():
        normalized = re.sub(rf"\b{word}\b", digit, normalized)
    normalized = _TITLE_CLEAN.sub(" ", normalized)
    return normalize_whitespace(normalized)


def _matches_ukri_title(candidate: str, ukri_titles: list[str]) -> bool:
    for ukri_title in ukri_titles:
        if candidate == ukri_title:
            return True
        if SequenceMatcher(None, candidate, ukri_title).ratio() >= 0.92:
            return True
    return False


@register_source("innovation_funding_search")
def _build_innovation_funding_search_source(settings: SourceSettings) -> Source:
    return InnovationFundingSearchSource(settings)
