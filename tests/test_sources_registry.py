from __future__ import annotations

from funding_slackbot.config import SourceSettings
from funding_slackbot.sources import create_source, registered_source_types
from funding_slackbot.sources.rss_source import (
    InnovationFundingSearchSource,
    LeverhulmeListingsSource,
    PortsmouthJobsSource,
    RssSource,
    WellcomeCmsSchemesSource,
    WellcomeSchemesSource,
)


def test_registered_source_types_include_modular_scrapers() -> None:
    assert set(registered_source_types()) >= {
        "innovation_funding_search",
        "leverhulme_listings",
        "portsmouth_jobs",
        "rss",
        "wellcome_cms_schemes",
        "wellcome_schemes",
    }


def test_create_source_builds_registered_scraper_classes() -> None:
    cases = [
        ("rss", RssSource),
        ("wellcome_schemes", WellcomeSchemesSource),
        ("wellcome_cms_schemes", WellcomeCmsSchemesSource),
        ("leverhulme_listings", LeverhulmeListingsSource),
        ("innovation_funding_search", InnovationFundingSearchSource),
        ("portsmouth_jobs", PortsmouthJobsSource),
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
