from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

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

_OPEN_CALL = re.compile(
    r'<div class="has-one-column open-call">(?P<call>.*?)</div>\s*<!---->\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(
    r'<h2[^>]+class=["\'][^"\']*\bopen-call__title\b[^"\']*["\'][^>]*>(?P<title>.*?)</h2>',
    re.IGNORECASE | re.DOTALL,
)
_TEXT = re.compile(
    r'<p[^>]+class=["\'][^"\']*\bopen-call__text\b[^"\']*["\'][^>]*>(?P<text>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_TYPE = re.compile(
    r'<p[^>]+class=["\'][^"\']*\bopen-call__type\b[^"\']*["\'][^>]*>(?P<type>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_LINK = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>\s*Learn more about this call',
    re.IGNORECASE | re.DOTALL,
)
_INFO_PAIR = re.compile(
    r'<p[^>]+class=["\'][^"\']*\bopen-call__info-title\b[^"\']*["\'][^>]*>(?P<label>.*?)</p>\s*'
    r'<p[^>]+class=["\'][^"\']*\bopen-call__info-text\b[^"\']*["\'][^>]*>(?P<value>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_AMOUNT = re.compile(r"(?:up to|up)\s+£[0-9][0-9,]*(?:k|m| million)?", re.IGNORECASE)


class AriaFundingOpportunitiesSource(Source):
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
            headers=default_headers(),
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        response.raise_for_status()

        opportunities: list[Opportunity] = []
        for call in _OPEN_CALL.findall(response.text):
            opportunity = self._call_to_opportunity(call)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _call_to_opportunity(self, call: str) -> Opportunity | None:
        title_match = _TITLE.search(call)
        link_match = _LINK.search(call)
        if title_match is None or link_match is None:
            return None
        text_match = _TEXT.search(call)
        type_match = _TYPE.search(call)
        title = html_to_text(title_match.group("title"))
        summary = html_to_text(text_match.group("text")) if text_match else ""
        info = {
            html_to_text(match.group("label")).lower(): html_to_text(match.group("value"))
            for match in _INFO_PAIR.finditer(call)
        }
        closing_date = parse_datetime_utc(info.get("deadline"))
        amount_match = _AMOUNT.search(summary)
        url = canonicalize_url(urljoin(self.url, link_match.group("href")))

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(None, url),
            title=title,
            url=url,
            published_at=None,
            summary=summary,
            raw=to_serializable_dict(info),
            closing_date=closing_date,
            opening_date=None,
            funder="ARIA",
            funding_type=html_to_text(type_match.group("type")) if type_match else None,
            total_fund=amount_match.group(0) if amount_match else None,
        )


@register_source("aria_funding_opportunities")
def _build_aria_funding_opportunities_source(settings: SourceSettings) -> Source:
    return AriaFundingOpportunitiesSource(settings)
