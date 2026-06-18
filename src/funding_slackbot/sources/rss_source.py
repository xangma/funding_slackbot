"""Backward-compatible imports for source implementations.

The concrete scrapers live in scraper-specific modules. Importing this module
still registers and exposes the historical class names used by tests and older
call sites.
"""

from __future__ import annotations

from .innovation import InnovationFundingSearchSource
from .leverhulme import LeverhulmeListingsSource
from .portsmouth_jobs import PortsmouthJobsSource
from .rss_feed import RssSource
from .wellcome import WellcomeCmsSchemesSource, WellcomeSchemesSource

__all__ = [
    "InnovationFundingSearchSource",
    "LeverhulmeListingsSource",
    "PortsmouthJobsSource",
    "RssSource",
    "WellcomeCmsSchemesSource",
    "WellcomeSchemesSource",
]
