from __future__ import annotations

from abc import ABC, abstractmethod

from funding_slackbot.models import DeadlineReminder, Opportunity, OpportunityDigest


class Notifier(ABC):
    @abstractmethod
    def post(self, opportunity: Opportunity, match_reason: str) -> None:
        """Send a matched opportunity to a destination."""

    def post_digest(self, digest: OpportunityDigest) -> None:
        """Send a grouped digest of matched opportunities."""
        for group in digest.groups:
            for item in group.items:
                self.post(item.opportunity, item.match_reason)

    def post_deadline_reminders(self, reminders: list[DeadlineReminder]) -> None:
        """Send deadline reminders for already-posted opportunities."""
        raise NotImplementedError("deadline reminders are not supported")
