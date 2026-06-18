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

_SECTION = re.compile(
    r'<h2[^>]+id=["\'](?P<id>[^"\']+)["\'][^>]*>(?P<title>.*?)</h2>(?P<body>.*?)(?=<h2[^>]+id=|<h3|$)',
    re.IGNORECASE | re.DOTALL,
)
_LINK = re.compile(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\']', re.IGNORECASE)
_MONEY = re.compile(
    r"(?:Total funding available|Funding):\s*([^.\n<]+(?:£|€)[^.\n<]*|£[0-9][^.\n<]*)",
    re.IGNORECASE,
)
_DATE_TEXT = re.compile(
    r"(?:deadline|closing date|closing dates)[^:]*:\s*(?P<value>[^<]+)",
    re.IGNORECASE,
)


class UkSpaceAgencyFundingSource(Source):
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
        for section in _SECTION.finditer(response.text):
            opportunity = self._section_to_opportunity(section)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _section_to_opportunity(self, section: re.Match[str]) -> Opportunity | None:
        title = html_to_text(section.group("title"))
        body = section.group("body")
        if not title or title.lower().startswith("closed opportunities"):
            return None

        link_match = _LINK.search(body)
        url = canonicalize_url(
            urljoin(self.url, link_match.group("href")) if link_match else self.url
        )
        text = html_to_text(body)
        closing_date = _latest_date_from_text(text)
        money_match = _MONEY.search(text)

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(None, f"{url}:{title}"),
            title=title,
            url=url,
            published_at=None,
            summary=_first_sentences(text, max_chars=700),
            raw=to_serializable_dict({"section_id": section.group("id")}),
            closing_date=closing_date,
            opening_date=None,
            funder="UK Space Agency",
            funding_type=_line_value(text, "Funding type"),
            total_fund=money_match.group(1).strip() if money_match else None,
        )


def _latest_date_from_text(text: str) -> datetime | None:
    candidates: list[datetime] = []
    for match in _DATE_TEXT.finditer(text):
        value = match.group("value")
        for part in re.split(r"\n|Full proposal deadline:|Outline Proposal:", value):
            parsed = parse_datetime_utc(part.strip(" ."))
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates) if candidates else None


def _line_value(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*(?P<value>[^\n]+)", text, re.IGNORECASE)
    return match.group("value").strip() if match else None


def _first_sentences(text: str, *, max_chars: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 1].rstrip()}..."


@register_source("uk_space_agency_funding")
def _build_uk_space_agency_funding_source(settings: SourceSettings) -> Source:
    return UkSpaceAgencyFundingSource(settings)
