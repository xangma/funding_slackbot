from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url

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

logger = logging.getLogger(__name__)

_LEVERHULME_ROW = re.compile(
    r"<tr>\s*<td>\s*<a[^>]+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>"
    r"(?P<title>.*?)</a>\s*</td>\s*<td>(?P<closing>.*?)</td>\s*</tr>",
    re.IGNORECASE | re.DOTALL,
)
_LEVERHULME_FULL_DATE = re.compile(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b")
_LEVERHULME_NON_FATAL_STATUS_CODES = {403, 404}


class LeverhulmeListingsSource(Source):
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
        response = self._fetch_page(self.url, headers)
        page_url = self.url
        rows: list[dict[str, str]] = []
        if response is not None:
            page_url = response.url
            rows = _extract_leverhulme_rows(response.text)

        if not rows:
            fallback_url = urljoin(page_url, "/closing-dates")
            if canonicalize_url(fallback_url) != canonicalize_url(page_url):
                fallback = self._fetch_page(fallback_url, headers)
                if fallback is not None:
                    page_url = fallback.url
                    rows = _extract_leverhulme_rows(fallback.text)

        if not rows and response is None:
            logger.warning(
                "Leverhulme source unavailable at %s; returning 0 opportunities",
                self.url,
            )
            return []

        opportunities: list[Opportunity] = []
        for row in rows:
            raw_url = urljoin(page_url, row["href"])
            url = canonicalize_url(raw_url)
            slug = row["href"].strip("/").replace("/", "-") or "scheme"
            closing_text = row["closing"]
            full_dates = _LEVERHULME_FULL_DATE.findall(closing_text)

            if not full_dates:
                continue

            seen_date_ids: set[str] = set()
            for date_text in full_dates:
                closing_date = parse_datetime_utc(date_text)
                date_id = (
                    closing_date.date().isoformat()
                    if closing_date
                    else normalize_whitespace(date_text)
                )
                if date_id in seen_date_ids:
                    continue
                seen_date_ids.add(date_id)
                opportunities.append(
                    Opportunity(
                        source_id=self.source_id,
                        external_id=f"leverhulme-scheme:{slug}:{date_id}",
                        title=row["title"],
                        url=url,
                        published_at=None,
                        summary=f"Leverhulme scheme closing date: {date_text}",
                        raw=to_serializable_dict(
                            {**row, "closing_date_text": date_text}
                        ),
                        closing_date=closing_date,
                        opening_date=None,
                        funder="Leverhulme Trust",
                        funding_type="Scheme",
                        total_fund=None,
                    )
                )

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _fetch_page(
        self,
        url_value: str,
        headers: dict[str, str],
    ) -> requests.Response | None:
        try:
            response = get_with_retries(
                url_value,
                timeout_seconds=self.timeout_seconds,
                headers=headers,
                max_attempts=self.retry_attempts,
                retry_backoff_seconds=self.retry_backoff_seconds,
            )
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in _LEVERHULME_NON_FATAL_STATUS_CODES:
                logger.warning(
                    "Leverhulme request returned %s for %s; trying fallback if available",
                    status_code,
                    url_value,
                )
                return None
            raise


def _extract_leverhulme_rows(page_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _LEVERHULME_ROW.finditer(page_text):
        href = normalize_whitespace(match.group("href"))
        title = html_to_text(match.group("title"))
        closing = html_to_text(match.group("closing"))
        if href and title:
            rows.append({"href": href, "title": title, "closing": closing})
    return rows


@register_source("leverhulme_listings")
def _build_leverhulme_listings_source(settings: SourceSettings) -> Source:
    return LeverhulmeListingsSource(settings)
