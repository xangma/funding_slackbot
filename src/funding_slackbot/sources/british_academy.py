from __future__ import annotations

import logging

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity

from ._common import browser_headers, get_with_retries, http_options
from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)


class BritishAcademyFundingSource(Source):
    """British Academy placeholder source.

    The funding pages are currently protected by Cloudflare for non-browser
    requests. This source fails soft so operators can enable it once a stable
    public endpoint is available without breaking the whole run.
    """

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
        if response.status_code == 403 and "cloudflare" in response.text.lower():
            logger.warning(
                "British Academy funding page is Cloudflare-protected; skipping"
            )
            return []
        response.raise_for_status()
        logger.warning("British Academy parser not enabled for current page structure")
        return []


@register_source("british_academy_funding")
def _build_british_academy_funding_source(settings: SourceSettings) -> Source:
    return BritishAcademyFundingSource(settings)
