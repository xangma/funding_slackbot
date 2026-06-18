from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from ._common import (
    browser_headers,
    get_with_retries,
    html_to_text,
    http_options,
    normalize_whitespace,
    to_serializable_dict,
)
from .base import Source
from .registry import register_source

_NIHR_CARD = re.compile(
    r'<div class="node node--type-funding-opportunity.*?</div>\s*</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_CARD_LINK = re.compile(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\']', re.IGNORECASE)
_CARD_TITLE = re.compile(r"<h3[^>]*>(?P<title>.*?)</h3>", re.IGNORECASE | re.DOTALL)
_CARD_TAG = re.compile(
    r'<p[^>]+class=["\'][^"\']*\btag\b[^"\']*["\'][^>]*>(?P<tag>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_CARD_SUMMARY = re.compile(
    r'field--name-field-teaser-copy.*?<div[^>]+class=["\'][^"\']*\bfield__item\b[^"\']*["\'][^>]*>(?P<summary>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TIME_FIELD = re.compile(
    r'field--name-field-(?P<field>start|end)-datetime.*?<time[^>]+datetime=["\'](?P<datetime>[^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)
_STATUS = re.compile(
    r'<div[^>]+class=["\'][^"\']*\bstatus\b[^"\']*["\'][^>]*>(?P<status>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


class NihrFundingOpportunitiesSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)

    def fetch(self) -> list[Opportunity]:
        response = get_with_retries(
            self.url,
            timeout_seconds=self.timeout_seconds,
            headers=browser_headers(),
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        response.raise_for_status()

        opportunities: list[Opportunity] = []
        for card in _NIHR_CARD.findall(response.text):
            opportunity = self._card_to_opportunity(card)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _card_to_opportunity(self, card: str) -> Opportunity | None:
        link_match = _CARD_LINK.search(card)
        title_match = _CARD_TITLE.search(card)
        if link_match is None or title_match is None:
            return None

        raw_url = urljoin(self.url, link_match.group("href"))
        url = canonicalize_url(raw_url)
        title = html_to_text(title_match.group("title"))
        tag_match = _CARD_TAG.search(card)
        status_match = _STATUS.search(card)
        summary_match = _CARD_SUMMARY.search(card)
        summary = html_to_text(summary_match.group("summary")) if summary_match else ""

        dates: dict[str, datetime | None] = {"start": None, "end": None}
        for match in _TIME_FIELD.finditer(card):
            dates[match.group("field")] = parse_datetime_utc(match.group("datetime"))

        raw = {
            "programme": html_to_text(tag_match.group("tag")) if tag_match else None,
            "status": html_to_text(status_match.group("status")) if status_match else None,
        }

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(None, url),
            title=title,
            url=url,
            published_at=dates["start"],
            summary=summary,
            raw=to_serializable_dict(raw),
            closing_date=dates["end"],
            opening_date=dates["start"],
            funder="NIHR",
            funding_type=normalize_whitespace(str(raw["programme"] or "")) or None,
            total_fund=None,
        )


@register_source("nihr_funding_opportunities")
def _build_nihr_funding_opportunities_source(settings: SourceSettings) -> Source:
    return NihrFundingOpportunitiesSource(settings)
