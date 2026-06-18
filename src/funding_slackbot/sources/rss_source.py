"""Backward-compatible imports for source implementations.

The concrete scrapers live in scraper-specific modules. Importing this module
still registers and exposes the historical class names used by tests and older
call sites.
"""

from __future__ import annotations

from .academy_medical_sciences import AcademyMedicalSciencesGrantSchemesSource
from .aria import AriaFundingOpportunitiesSource
from .british_academy import BritishAcademyFundingSource
from .cancer_research import CancerResearchHorizonsFundingSource
from .horizon_europe import HorizonEuropeFundingSource
from .innovation import InnovationFundingSearchSource
from .leverhulme import LeverhulmeListingsSource
from .nihr import NihrFundingOpportunitiesSource
from .portsmouth_jobs import PortsmouthJobsSource
from .raeng import RoyalAcademyEngineeringProgrammesSource
from .rss_feed import RssSource
from .royal_society import RoyalSocietyApplicationDatesSource
from .uk_space_agency import UkSpaceAgencyFundingSource
from .wellcome import WellcomeCmsSchemesSource, WellcomeSchemesSource

__all__ = [
    "AcademyMedicalSciencesGrantSchemesSource",
    "AriaFundingOpportunitiesSource",
    "BritishAcademyFundingSource",
    "CancerResearchHorizonsFundingSource",
    "HorizonEuropeFundingSource",
    "InnovationFundingSearchSource",
    "LeverhulmeListingsSource",
    "NihrFundingOpportunitiesSource",
    "PortsmouthJobsSource",
    "RoyalAcademyEngineeringProgrammesSource",
    "RoyalSocietyApplicationDatesSource",
    "RssSource",
    "UkSpaceAgencyFundingSource",
    "WellcomeCmsSchemesSource",
    "WellcomeSchemesSource",
]
