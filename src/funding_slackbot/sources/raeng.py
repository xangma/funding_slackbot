from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
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

_CARD = re.compile(
    r'<div class="card__wrapper">(?P<card>.*?)</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(
    r'<h2[^>]+class=["\'][^"\']*\bcard-title\b[^"\']*["\'][^>]*>(?P<title>.*?)</h2>',
    re.IGNORECASE | re.DOTALL,
)
_TEXT = re.compile(
    r'<p[^>]+class=["\'][^"\']*\bcard-text\b[^"\']*["\'][^>]*>(?P<text>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_LINK = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*class=["\'][^"\']*\bbtn\b',
    re.IGNORECASE | re.DOTALL,
)


class RoyalAcademyEngineeringProgrammesSource(Source):
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
        for card in _CARD.findall(response.text):
            title_match = _TITLE.search(card)
            link_match = _LINK.search(card)
            if title_match is None or link_match is None:
                continue
            text_match = _TEXT.search(card)
            url = canonicalize_url(urljoin(self.url, link_match.group("href")))
            title = html_to_text(title_match.group("title"))
            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=derive_external_id(None, url),
                    title=title,
                    url=url,
                    published_at=None,
                    summary=html_to_text(text_match.group("text")) if text_match else "",
                    raw=to_serializable_dict({"source_page": self.url}),
                    closing_date=None,
                    opening_date=None,
                    funder="Royal Academy of Engineering",
                    funding_type="Programme",
                    total_fund=None,
                )
            )

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities


@register_source("raeng_programmes")
def _build_raeng_programmes_source(settings: SourceSettings) -> Source:
    return RoyalAcademyEngineeringProgrammesSource(settings)
