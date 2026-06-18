from __future__ import annotations

from funding_slackbot.config import SourceSettings
from funding_slackbot.sources import create_source, registered_source_types
from funding_slackbot.sources.rss_source import (
    AcademyMedicalSciencesGrantSchemesSource,
    AriaFundingOpportunitiesSource,
    BritishAcademyFundingSource,
    CancerResearchHorizonsFundingSource,
    HorizonEuropeFundingSource,
    InnovationFundingSearchSource,
    LeverhulmeListingsSource,
    NihrFundingOpportunitiesSource,
    PortsmouthJobsSource,
    RoyalAcademyEngineeringProgrammesSource,
    RoyalSocietyApplicationDatesSource,
    RssSource,
    UkSpaceAgencyFundingSource,
    WellcomeCmsSchemesSource,
    WellcomeSchemesSource,
)


def test_registered_source_types_include_all_scrapers() -> None:
    assert set(registered_source_types()) >= {
        "rss",
        "wellcome_schemes",
        "wellcome_cms_schemes",
        "leverhulme_listings",
        "innovation_funding_search",
        "portsmouth_jobs",
        "aria_funding_opportunities",
        "british_academy_funding",
        "nihr_funding_opportunities",
        "cancer_research_horizons_funding",
        "horizon_europe_funding",
        "uk_space_agency_funding",
        "raeng_programmes",
        "royal_society_application_dates",
        "academy_medical_sciences_grants",
    }


def test_create_source_builds_registered_scraper_classes() -> None:
    cases = [
        ("rss", RssSource),
        ("wellcome_schemes", WellcomeSchemesSource),
        ("wellcome_cms_schemes", WellcomeCmsSchemesSource),
        ("leverhulme_listings", LeverhulmeListingsSource),
        ("innovation_funding_search", InnovationFundingSearchSource),
        ("portsmouth_jobs", PortsmouthJobsSource),
        ("aria_funding_opportunities", AriaFundingOpportunitiesSource),
        ("british_academy_funding", BritishAcademyFundingSource),
        ("nihr_funding_opportunities", NihrFundingOpportunitiesSource),
        ("cancer_research_horizons_funding", CancerResearchHorizonsFundingSource),
        ("horizon_europe_funding", HorizonEuropeFundingSource),
        ("uk_space_agency_funding", UkSpaceAgencyFundingSource),
        ("raeng_programmes", RoyalAcademyEngineeringProgrammesSource),
        ("royal_society_application_dates", RoyalSocietyApplicationDatesSource),
        ("academy_medical_sciences_grants", AcademyMedicalSciencesGrantSchemesSource),
    ]

    for source_type, expected_class in cases:
        source = create_source(
            SourceSettings(
                id=f"{source_type}_source",
                type=source_type,
                url="https://example.test/source",
            )
        )
        assert isinstance(source, expected_class)
