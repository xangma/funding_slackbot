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
