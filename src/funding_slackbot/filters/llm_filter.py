from __future__ import annotations

import logging

from funding_slackbot.config import FilterSettings
from funding_slackbot.filters.base import Filter, FilterResult
from funding_slackbot.llm import LLMError, LocalLLMClient
from funding_slackbot.models import Opportunity

from .keyword_filter import RuleBasedFilter

logger = logging.getLogger(__name__)


class LLMAssessmentFilter(Filter):
    """Filter that uses local LLM assessment with rule-based fallback.

    When the LLM is enabled and reachable, it classifies each opportunity
    using normalized fields. On any failure (network error, bad JSON,
    unreachable model) it falls back to the existing RuleBasedFilter.
    """

    def __init__(
        self,
        settings: FilterSettings,
        *,
        llm_client: LocalLLMClient | None = None,
        llm_assessment_enabled: bool = False,
    ) -> None:
        self.settings = settings
        self._rule_filter = RuleBasedFilter(settings)
        self._llm_client = llm_client
        self._llm_enabled = llm_assessment_enabled

    def evaluate(self, opportunity: Opportunity) -> FilterResult:
        if not self._should_use_llm():
            return self._rule_filter.evaluate(opportunity)

        try:
            result = self._llm_client.assess_opportunity(
                opportunity,
                criteria=self._assessment_criteria(),
            )
            if result is not None:
                logger.debug(
                    "LLM assessment for %s: matched=%s reason=%s",
                    opportunity.external_id,
                    result.matched,
                    result.reason,
                )
                return FilterResult(
                    matched=result.matched,
                    reasons=[result.reason],
                    assessment_summary=result.summary,
                    requirements=result.requirements,
                    considerations=result.considerations,
                )
        except LLMError as exc:
            logger.warning(
                "LLM assessment failed for %s, falling back to rules: %s",
                opportunity.external_id,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Unexpected error during LLM assessment for %s, falling back to rules: %s",
                opportunity.external_id,
                exc,
            )

        logger.debug(
            "Falling back to rule-based filter for %s",
            opportunity.external_id,
        )
        return self._rule_filter.evaluate(opportunity)

    def _should_use_llm(self) -> bool:
        return self._llm_enabled and self._llm_client is not None

    def _assessment_criteria(self) -> dict[str, object]:
        criteria: dict[str, object] = {}
        if self.settings.include_keywords:
            criteria["include_keywords"] = self.settings.include_keywords
        if self.settings.exclude_keywords:
            criteria["exclude_keywords"] = self.settings.exclude_keywords
        if self.settings.include_councils:
            criteria["include_councils"] = self.settings.include_councils
        if self.settings.include_funding_types:
            criteria["include_funding_types"] = self.settings.include_funding_types
        if self.settings.min_days_until_deadline is not None:
            criteria["min_days_until_deadline"] = self.settings.min_days_until_deadline
        return criteria
