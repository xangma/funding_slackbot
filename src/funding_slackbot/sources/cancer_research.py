from __future__ import annotations

import re
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

_SECTION = re.compile(
    r'<section[^>]+aria-labelledby=["\'](?P<label_id>[^"\']+)["\'][^>]*>(?P<section>.*?)</section>',
    re.IGNORECASE | re.DOTALL,
)
_HEADING = re.compile(r"<h2[^>]*>(?P<title>.*?)</h2>", re.IGNORECASE | re.DOTALL)
_LINK = re.compile(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\']', re.IGNORECASE)
_FUNDING_AMOUNT = re.compile(
    r"Funding amount\s+(?P<amount>.*?)(?:Learn more|Find out more|$)",
    re.IGNORECASE | re.DOTALL,
)


class CancerResearchHorizonsFundingSource(Source):
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
        for section_match in _SECTION.finditer(response.text):
            opportunity = self._section_to_opportunity(section_match.group("section"))
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities

    def _section_to_opportunity(self, section: str) -> Opportunity | None:
        heading_match = _HEADING.search(section)
        if heading_match is None:
            return None
        title = html_to_text(heading_match.group("title"))
        if title.lower() in {"funding translation", "what we offer"}:
            return None
        text = html_to_text(section)
        if "funding amount" not in text.lower() and "funding" not in title.lower():
            return None
        link_match = _LINK.search(section)
        url = canonicalize_url(
            urljoin(self.url, link_match.group("href")) if link_match else self.url
        )
        amount_match = _FUNDING_AMOUNT.search(text)
        amount = html_to_text(amount_match.group("amount")) if amount_match else None

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(None, f"{url}:{title}"),
            title=title,
            url=url,
            published_at=None,
            summary=_first_sentences(text, max_chars=700),
            raw=to_serializable_dict({"source_page": self.url}),
            closing_date=None,
            opening_date=None,
            funder="Cancer Research Horizons",
            funding_type="Translational funding",
            total_fund=amount,
        )


def _first_sentences(text: str, *, max_chars: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 1].rstrip()}..."


@register_source("cancer_research_horizons_funding")
def _build_cancer_research_horizons_funding_source(
    settings: SourceSettings,
) -> Source:
    return CancerResearchHorizonsFundingSource(settings)
