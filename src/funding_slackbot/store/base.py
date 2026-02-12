from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SeenRecord:
    external_id: str
    source_id: str
    first_seen_at: datetime
    posted_at: datetime | None
    title: str
    url: str
    match_reason: str | None


class Store(ABC):
    @abstractmethod
    def init_db(self) -> None:
        """Create any required schema."""

    @abstractmethod
    def has_seen(self, external_id: str) -> SeenRecord | None:
        """Return existing record if seen, otherwise None."""

    @abstractmethod
    def mark_seen(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str | None,
        posted_at: datetime | None,
    ) -> None:
        """Create or update seen record, optionally marking posted_at."""
