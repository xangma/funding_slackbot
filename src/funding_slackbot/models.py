from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Opportunity:
    source_id: str
    external_id: str
    title: str
    url: str
    published_at: datetime | None
    summary: str
    raw: dict[str, Any] = field(default_factory=dict)
    closing_date: datetime | None = None
    opening_date: datetime | None = None
    funder: str | None = None
    funding_type: str | None = None
    total_fund: str | None = None


@dataclass(slots=True)
class OpportunityMatch:
    opportunity: Opportunity
    match_reason: str
    assessment_summary: str = ""
    requirements: list[str] = field(default_factory=list)
    considerations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OpportunityGroup:
    heading: str
    summary: str
    items: list[OpportunityMatch]


@dataclass(slots=True)
class OpportunityDigest:
    title: str
    introduction: str
    groups: list[OpportunityGroup]
    generated_by_llm: bool = False


@dataclass(slots=True)
class DeadlineReminder:
    opportunity: Opportunity
    match_reason: str | None
    original_posted_at: datetime | None
