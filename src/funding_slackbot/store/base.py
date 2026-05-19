from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

PostStatus = Literal["seen", "posting", "posted", "post_failed"]


@dataclass(slots=True)
class SeenRecord:
    external_id: str
    source_id: str
    first_seen_at: datetime
    posted_at: datetime | None
    title: str
    url: str
    match_reason: str | None
    post_status: PostStatus
    last_post_attempt_at: datetime | None
    post_error: str | None


class Store(ABC):
    @abstractmethod
    def init_db(self) -> None:
        """Create any required schema."""

    @abstractmethod
    def has_seen(self, *, source_id: str, external_id: str) -> SeenRecord | None:
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

    @abstractmethod
    def claim_for_post(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str,
    ) -> bool:
        """Reserve a record for posting. Return False if it is already reserved or posted."""

    @abstractmethod
    def mark_posted(
        self,
        *,
        external_id: str,
        source_id: str,
        match_reason: str,
        posted_at: datetime,
    ) -> None:
        """Mark a reserved record as posted."""

    @abstractmethod
    def mark_post_failed(
        self,
        *,
        external_id: str,
        source_id: str,
        error: str,
    ) -> None:
        """Record a failed post attempt so a later run can retry."""
