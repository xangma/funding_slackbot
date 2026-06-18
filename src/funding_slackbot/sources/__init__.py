"""Source implementations and registry."""

from .academy_medical_sciences import AcademyMedicalSciencesGrantSchemesSource
from .aria import AriaFundingOpportunitiesSource
from .base import Source
from .british_academy import BritishAcademyFundingSource
from .cancer_research import CancerResearchHorizonsFundingSource
from .horizon_europe import HorizonEuropeFundingSource
from .innovation import InnovationFundingSearchSource
from .leverhulme import LeverhulmeListingsSource
from .nihr import NihrFundingOpportunitiesSource
from .portsmouth_jobs import PortsmouthJobsSource
from .raeng import RoyalAcademyEngineeringProgrammesSource
from .registry import (
    SourceRegistrationError,
    create_source,
    register_source,
    registered_source_types,
)
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
    "Source",
    "SourceRegistrationError",
    "UkSpaceAgencyFundingSource",
    "WellcomeCmsSchemesSource",
    "WellcomeSchemesSource",
    "RssSource",
    "create_source",
    "register_source",
    "registered_source_types",
]
