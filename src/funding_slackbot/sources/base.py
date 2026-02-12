from __future__ import annotations

from abc import ABC, abstractmethod

from funding_slackbot.models import Opportunity


class Source(ABC):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    @abstractmethod
    def fetch(self) -> list[Opportunity]:
        """Fetch and normalize opportunities from the source."""
