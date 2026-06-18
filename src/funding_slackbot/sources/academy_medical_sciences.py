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

_ARTICLE = re.compile(
    r'<article[^>]+class=["\'][^"\']*\bboxgrid\b[^"\']*["\'][^>]*>(?P<article>.*?)</article>',
    re.IGNORECASE | re.DOTALL,
)
_ARTICLE_LINK = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>\s*<h3[^>]*>(?P<title>.*?)</h3>',
    re.IGNORECASE | re.DOTALL,
)


class AcademyMedicalSciencesGrantSchemesSource(Source):
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
        for article in _ARTICLE.findall(response.text):
            match = _ARTICLE_LINK.search(article)
            if match is None:
                continue
            title = html_to_text(match.group("title"))
            url = canonicalize_url(urljoin(self.url, match.group("href")))
            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=derive_external_id(None, url),
                    title=title,
                    url=url,
                    published_at=None,
                    summary="Academy of Medical Sciences grant scheme.",
                    raw=to_serializable_dict({"source_page": self.url}),
                    closing_date=None,
                    opening_date=None,
                    funder="Academy of Medical Sciences",
                    funding_type="Grant scheme",
                    total_fund=None,
                )
            )

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities


@register_source("academy_medical_sciences_grants")
def _build_academy_medical_sciences_grants_source(
    settings: SourceSettings,
) -> Source:
    return AcademyMedicalSciencesGrantSchemesSource(settings)
