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

_TABLE_ROW = re.compile(r"<tr[^>]*>(?P<row>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.IGNORECASE | re.DOTALL)
_ANCHOR = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


class RoyalSocietyApplicationDatesSource(Source):
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
        for row_match in _TABLE_ROW.finditer(response.text):
            opportunity = self._row_to_opportunity(row_match.group("row"))
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _row_to_opportunity(self, row: str) -> Opportunity | None:
        cells = [match.group("cell") for match in _TABLE_CELL.finditer(row)]
        if len(cells) < 3:
            return None
        link = _ANCHOR.search(cells[0])
        if link is None:
            return None

        title = html_to_text(link.group("label"))
        url = canonicalize_url(urljoin(self.url, link.group("href")))
        opening_date = parse_datetime_utc(html_to_text(cells[1]))
        closing_date = parse_datetime_utc(html_to_text(cells[2]))
        decision = html_to_text(cells[3]) if len(cells) > 3 else ""
        summary = "Royal Society grant application dates."
        if decision:
            summary = f"{summary} Decision expected: {decision}."

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(None, f"{url}:{closing_date or title}"),
            title=title,
            url=url,
            published_at=opening_date,
            summary=summary,
            raw=to_serializable_dict(
                {
                    "open_date_text": html_to_text(cells[1]),
                    "close_date_text": html_to_text(cells[2]),
                    "decision_text": decision,
                }
            ),
            closing_date=closing_date,
            opening_date=opening_date,
            funder="Royal Society",
            funding_type="Grant scheme",
            total_fund=None,
        )


@register_source("royal_society_application_dates")
def _build_royal_society_application_dates_source(
    settings: SourceSettings,
) -> Source:
    return RoyalSocietyApplicationDatesSource(settings)
