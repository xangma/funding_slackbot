from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc

from ._common import (
    default_headers,
    http_options,
    normalize_whitespace,
    positive_int_option,
    to_serializable_dict,
)
from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)

_SEARCH_API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
_HORIZON_FRAMEWORK_ID = "43108390"
_OPEN_STATUS_IDS = {"31094501", "31094502"}
_DEFAULT_SEARCH_TERMS = (
    "artificial intelligence",
    "health",
    "robotics",
    "bioinformatics",
    "climate",
    "space",
)


class HorizonEuropeFundingSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url or _SEARCH_API_URL
        self.search_url = str(settings.options.get("search_url", _SEARCH_API_URL))
        search_terms = settings.options.get("search_terms", _DEFAULT_SEARCH_TERMS)
        if isinstance(search_terms, list):
            self.search_terms = [str(term).strip() for term in search_terms if str(term).strip()]
        else:
            self.search_terms = list(_DEFAULT_SEARCH_TERMS)
        self.page_size = positive_int_option(
            settings.options.get("page_size", 50),
            field_name=f"sources.{settings.id}.page_size",
        )
        self.max_results = positive_int_option(
            settings.options.get("max_results", 50),
            field_name=f"sources.{settings.id}.max_results",
        )
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)

    def fetch(self) -> list[Opportunity]:
        opportunities_by_id: dict[str, Opportunity] = {}
        for term in self.search_terms:
            payload = self._search(term)
            for result in payload.get("results", []):
                opportunity = self._result_to_opportunity(result)
                if opportunity is not None:
                    opportunities_by_id[opportunity.external_id] = opportunity
                    if len(opportunities_by_id) >= self.max_results:
                        break
            if len(opportunities_by_id) >= self.max_results:
                break

        opportunities = list(opportunities_by_id.values())
        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _search(self, term: str) -> dict[str, Any]:
        last_error: Exception | None = None
        data = {
            "apiKey": "SEDIA",
            "text": term,
            "pageSize": str(self.page_size),
            "pageNumber": "1",
        }
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = requests.post(
                    self.search_url,
                    data=data,
                    headers=default_headers(),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.retry_attempts:
                    break
        logger.warning("Horizon Europe search failed for %r: %s", term, last_error)
        return {}

    def _result_to_opportunity(self, result: dict[str, Any]) -> Opportunity | None:
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            return None
        if _first(metadata.get("frameworkProgramme")) != _HORIZON_FRAMEWORK_ID:
            return None
        if _first(metadata.get("status")) not in _OPEN_STATUS_IDS:
            return None

        identifier = _first(metadata.get("identifier"))
        if not identifier:
            return None
        title = normalize_whitespace(str(result.get("summary") or identifier))
        closing_date = parse_datetime_utc(_first(metadata.get("deadlineDate")))
        opening_date = parse_datetime_utc(_first(metadata.get("startDate")))
        action_type = _first(metadata.get("typeOfAction")) or _first(
            metadata.get("typeOfActionLabel")
        )
        topic_url = (
            "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
            f"screen/opportunities/topic-details/{identifier}"
        )
        call_identifier = _first(metadata.get("callIdentifier"))

        return Opportunity(
            source_id=self.source_id,
            external_id=identifier,
            title=title,
            url=topic_url,
            published_at=opening_date,
            summary=(
                "Horizon Europe funding topic open or forthcoming for UK applicants. "
                f"Call: {call_identifier or 'unknown'}."
            ),
            raw=to_serializable_dict(result),
            closing_date=closing_date,
            opening_date=opening_date,
            funder="European Commission Horizon Europe",
            funding_type=action_type or "Horizon Europe topic",
            total_fund=None,
        )


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0]).strip() or None
    if isinstance(value, str):
        return value.strip() or None
    return None


@register_source("horizon_europe_funding")
def _build_horizon_europe_funding_source(settings: SourceSettings) -> Source:
    return HorizonEuropeFundingSource(settings)
