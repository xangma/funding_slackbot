from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from funding_slackbot.models import Opportunity


@dataclass(slots=True)
class FilterResult:
    matched: bool
    reasons: list[str] = field(default_factory=list)

    def reason_text(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "no specific reason"


class Filter(ABC):
    @abstractmethod
    def evaluate(self, opportunity: Opportunity) -> FilterResult:
        """Evaluate an opportunity and return match decision with reasons."""

    def matches(self, opportunity: Opportunity) -> bool:
        return self.evaluate(opportunity).matched
