from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

from funding_slackbot.config import FilterSettings
from funding_slackbot.models import Opportunity

from .base import Filter, FilterResult


class RuleBasedFilter(Filter):
    def __init__(
        self,
        settings: FilterSettings,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.now_provider = now_provider or _utcnow

    def evaluate(self, opportunity: Opportunity) -> FilterResult:
        reasons: list[str] = []

        searchable = f"{opportunity.title}\n{opportunity.summary}"

        include_hits = _find_hits(self.settings.include_keywords, searchable)
        if self.settings.include_keywords and not include_hits:
            return FilterResult(matched=False, reasons=["no include keywords matched"])
        if include_hits:
            reasons.append(f"keywords: {', '.join(include_hits)}")

        exclude_hits = _find_hits(self.settings.exclude_keywords, searchable)
        if exclude_hits:
            return FilterResult(
                matched=False,
                reasons=[f"excluded by keyword: {', '.join(exclude_hits)}"],
            )

        if self.settings.include_councils:
            normalized_councils = {value.lower() for value in self.settings.include_councils}
            funder_value = (opportunity.funder or "").lower()
            if not any(council in funder_value for council in normalized_councils):
                return FilterResult(matched=False, reasons=["funder/council filter not matched"])
            reasons.append(f"council/funder: {opportunity.funder}")

        if self.settings.include_funding_types:
            normalized_funding_types = {
                value.lower() for value in self.settings.include_funding_types
            }
            funding_value = (opportunity.funding_type or "").lower()
            if not any(
                funding_type in funding_value
                for funding_type in normalized_funding_types
            ):
                return FilterResult(
                    matched=False,
                    reasons=["funding_type filter not matched"],
                )
            reasons.append(f"funding type: {opportunity.funding_type}")

        if self.settings.min_days_until_deadline is not None:
            if opportunity.closing_date is None:
                return FilterResult(
                    matched=False,
                    reasons=["missing closing date required by deadline filter"],
                )

            now = self.now_provider().astimezone(timezone.utc)
            delta = opportunity.closing_date - now
            days_until_deadline = int(delta.total_seconds() // 86400)
            if days_until_deadline < self.settings.min_days_until_deadline:
                return FilterResult(
                    matched=False,
                    reasons=[
                        "deadline too soon "
                        f"({days_until_deadline}d < {self.settings.min_days_until_deadline}d)"
                    ],
                )
            reasons.append(f"deadline in {days_until_deadline} days")

        if not reasons:
            reasons.append("matched default pass-through rules")

        return FilterResult(matched=True, reasons=reasons)


def _find_hits(keywords: list[str], searchable: str) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        pattern = _build_keyword_pattern(keyword)
        if pattern and pattern.search(searchable):
            hits.append(keyword)
    return hits


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    normalized = " ".join(keyword.strip().split())
    if not normalized:
        return None

    parts = [_build_keyword_part_pattern(part) for part in normalized.split(" ")]
    phrase = r"\s+".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])", re.IGNORECASE)


def _build_keyword_part_pattern(part: str) -> str:
    if "*" not in part:
        return re.escape(part)

    wildcard = r"[A-Za-z0-9_-]*"
    segments = [re.escape(segment) for segment in part.split("*")]
    return wildcard.join(segments)
