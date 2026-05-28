from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

PostStatus = Literal[
    "seen",
    "pending_digest",
    "posting",
    "posted",
    "post_failed",
]
ReminderStatus = Literal["none", "posting", "posted", "reminder_failed"]


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
    last_seen_at: datetime | None = None
    closing_date: datetime | None = None
    opening_date: datetime | None = None
    funder: str | None = None
    funding_type: str | None = None
    total_fund: str | None = None
    reminder_status: ReminderStatus = "none"
    last_reminder_attempt_at: datetime | None = None
    reminder_posted_at: datetime | None = None
    reminder_error: str | None = None


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
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
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
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
    ) -> bool:
        """Reserve a record for posting. Return False if it is already reserved or posted."""

    @abstractmethod
    def queue_for_digest(
        self,
        *,
        external_id: str,
        source_id: str,
        title: str,
        url: str,
        match_reason: str,
        queued_at: datetime,
        closing_date: datetime | None = None,
        opening_date: datetime | None = None,
        funder: str | None = None,
        funding_type: str | None = None,
        total_fund: str | None = None,
    ) -> bool:
        """Queue a matching record for a later grouped digest."""

    @abstractmethod
    def list_pending_digest(
        self,
        *,
        limit: int,
    ) -> list[SeenRecord]:
        """Return opportunities queued for a grouped digest."""

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

    @abstractmethod
    def list_due_deadline_reminders(
        self,
        *,
        now: datetime,
        days_before_deadline: int,
        limit: int,
    ) -> list[SeenRecord]:
        """Return posted opportunities with an unposted deadline reminder due."""

    @abstractmethod
    def claim_deadline_reminder(
        self,
        *,
        external_id: str,
        source_id: str,
    ) -> bool:
        """Reserve a due deadline reminder for posting."""

    @abstractmethod
    def mark_deadline_reminder_posted(
        self,
        *,
        external_id: str,
        source_id: str,
        posted_at: datetime,
    ) -> None:
        """Mark a reminder as posted."""

    @abstractmethod
    def mark_deadline_reminder_failed(
        self,
        *,
        external_id: str,
        source_id: str,
        error: str,
    ) -> None:
        """Record a failed reminder post attempt so a later run can retry."""
