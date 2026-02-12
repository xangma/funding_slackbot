from __future__ import annotations

from abc import ABC, abstractmethod

from funding_slackbot.models import Opportunity


class Notifier(ABC):
    @abstractmethod
    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        """Send a matched opportunity to a destination."""
