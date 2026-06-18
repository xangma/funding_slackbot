"""Tests for new funding source parsers and Leverhulme closing-date filter."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.sources.aria import AriaFundingOpportunitiesSource
from funding_slackbot.sources.british_academy import BritishAcademyFundingSource
from funding_slackbot.sources.cancer_research import CancerResearchHorizonsFundingSource
from funding_slackbot.sources.horizon_europe import HorizonEuropeFundingSource
from funding_slackbot.sources.leverhulme import LeverhulmeListingsSource
from funding_slackbot.sources.nihr import NihrFundingOpportunitiesSource
from funding_slackbot.sources.raeng import RoyalAcademyEngineeringProgrammesSource
from funding_slackbot.sources.royal_society import RoyalSocietyApplicationDatesSource
from funding_slackbot.sources.uk_space_agency import UkSpaceAgencyFundingSource
from funding_slackbot.sources.academy_medical_sciences import AcademyMedicalSciencesGrantSchemesSource


class _DummyResponse:
    def __init__(
        self,
        content: bytes,
        url: str = "https://example.test/source",
        status_code: int = 200,
    ) -> None:
        self.content = content
        self.text = content.decode("utf-8")
        self.url = url
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error: test fixture",
                response=self,
            )

    def json(self):
        return json.loads(self.text)


# ---------------------------------------------------------------------------
# Leverhulme - closing_date before today is skipped
# ---------------------------------------------------------------------------

class TestLeverhulmeClosingDateFilter:
    def test_skips_past_but_keeps_same_day_and_future_dates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        html = b"""
        <html><body>
          <table>
            <tr><td><a href="/scheme-a">Scheme A</a></td><td>10 January 2026</td></tr>
            <tr><td><a href="/scheme-b">Scheme B</a></td><td>15 March 2026</td></tr>
            <tr><td><a href="/scheme-b">Scheme B</a></td><td>10 June 2026</td></tr>
          </table>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html, url="https://www.leverhulme.ac.uk/closing-dates"),
        )
        source = LeverhulmeListingsSource(
            SourceSettings(
                id="leverhulme_listings",
                type="leverhulme_listings",
                url="https://www.leverhulme.ac.uk/listings",
            ),
            now_provider=lambda: datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        )
        opportunities = source.fetch()
        titles = [o.title for o in opportunities]
        assert "Scheme A" not in titles
        assert titles == ["Scheme B", "Scheme B"]

    def test_includes_future_closing_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
          <table>
            <tr><td><a href="/scheme-future">Future Scheme</a></td><td>1 December 2026</td></tr>
          </table>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html, url="https://www.leverhulme.ac.uk/closing-dates"),
        )
        source = LeverhulmeListingsSource(
            SourceSettings(
                id="leverhulme_listings",
                type="leverhulme_listings",
                url="https://www.leverhulme.ac.uk/listings",
            ),
            now_provider=lambda: datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Future Scheme"

    def test_skips_all_when_every_date_is_past(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
          <table>
            <tr><td><a href="/old-1">Old 1</a></td><td>1 January 2025</td></tr>
            <tr><td><a href="/old-2">Old 2</a></td><td>31 December 2025</td></tr>
          </table>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html, url="https://www.leverhulme.ac.uk/closing-dates"),
        )
        source = LeverhulmeListingsSource(
            SourceSettings(
                id="leverhulme_listings",
                type="leverhulme_listings",
                url="https://www.leverhulme.ac.uk/listings",
            ),
            now_provider=lambda: datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        )
        assert source.fetch() == []


# ---------------------------------------------------------------------------
# ARIA - parse open-call blocks
# ---------------------------------------------------------------------------

class TestAriaFundingOpportunitiesSource:
    def test_parses_open_call_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <div class="has-one-column open-call">
          <h2 class="open-call__title">Test Grant Call</h2>
          <p class="open-call__text">Up to \xc2\xa3500k for AI research.</p>
          <p class="open-call__type">Fellowship</p>
          <p class="open-call__info-title">Deadline</p>
          <p class="open-call__info-text">30 June 2026</p>
          <a href="/funding/test-grant">Learn more about this call</a>
        </div>
        <!---->
        </div>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = AriaFundingOpportunitiesSource(
            SourceSettings(
                id="aria_funding",
                type="aria_funding_opportunities",
                url="https://www.artificial-intelligence-research.ac.uk/funding-opportunities/",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Test Grant Call"
        assert opportunities[0].funder == "ARIA"
        assert opportunities[0].closing_date is not None
        assert opportunities[0].total_fund is not None


# ---------------------------------------------------------------------------
# British Academy - Cloudflare soft-fail
# ---------------------------------------------------------------------------

class TestBritishAcademyFundingSource:
    def test_returns_empty_on_cloudflare_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(
                b"cloudflare turnstile challenge",
                status_code=403,
            ),
        )
        source = BritishAcademyFundingSource(
            SourceSettings(
                id="british_academy",
                type="british_academy_funding",
                url="https://www.britishacademy.ac.uk/funding-search/",
            )
        )
        assert source.fetch() == []


# ---------------------------------------------------------------------------
# NIHR - parse funding cards
# ---------------------------------------------------------------------------

class TestNihrFundingOpportunitiesSource:
    def test_parses_cards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <div class="node node--type-funding-opportunity">
          <h3>NIHR Test Opportunity</h3>
          <a href="/funding/nihr-test">Details</a>
          <p class="tag">Health Research</p>
          <div class="status">Open</div>
          <div class="field--name-field-teaser-copy">
            <div class="field__item"><p>Test summary text.</p></div>
          </div>
          <div class="field--name-field-start-datetime">
            <time datetime="2026-01-01T00:00:00Z">1 Jan 2026</time>
          </div>
          <div class="field--name-field-end-datetime">
            <time datetime="2026-06-30T23:59:00Z">30 Jun 2026</time>
          </div>
        </div>
        </div>
        </div>
        </div>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = NihrFundingOpportunitiesSource(
            SourceSettings(
                id="nihr_funding",
                type="nihr_funding_opportunities",
                url="https://www.nihr.ac.uk/funding-opportunities",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "NIHR Test Opportunity"
        assert opportunities[0].funder == "NIHR"
        assert opportunities[0].closing_date is not None


# ---------------------------------------------------------------------------
# Cancer Research Horizons - parse sections
# ---------------------------------------------------------------------------

class TestCancerResearchHorizonsFundingSource:
    def test_parses_funding_sections(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <section aria-labelledby="translational">
          <h2>Translational Research Funding</h2>
          <p>Funding amount up to \xc2\xa3200,000 for translational projects.</p>
          <a href="/funding/translational">Learn more</a>
        </section>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = CancerResearchHorizonsFundingSource(
            SourceSettings(
                id="crh_funding",
                type="cancer_research_horizons_funding",
                url="https://cancerresearchhorizons.org.uk/funding",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Translational Research Funding"
        assert opportunities[0].funder == "Cancer Research Horizons"


# ---------------------------------------------------------------------------
# Horizon Europe - parse API results
# ---------------------------------------------------------------------------

class TestHorizonEuropeFundingSource:
    def test_parses_api_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "results": [
                {
                    "summary": "H2020-TEST-001 - AI in Healthcare",
                    "metadata": {
                        "frameworkProgramme": ["43108390"],
                        "status": ["31094501"],
                        "identifier": ["H2020-TEST-001"],
                        "deadlineDate": ["2026-09-30"],
                        "startDate": ["2026-03-01"],
                        "typeOfAction": ["RIA"],
                        "callIdentifier": ["HORIZON-CL4-2026"],
                    },
                }
            ]
        }
        monkeypatch.setattr(
            "requests.post",
            lambda *a, **k: _DummyResponse(json.dumps(payload).encode("utf-8")),
        )
        source = HorizonEuropeFundingSource(
            SourceSettings(
                id="horizon_eu",
                type="horizon_europe_funding",
                url="https://api.tech.ec.europa.eu/search-api/prod/rest/search",
                options={"search_terms": ["health"]},
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].external_id == "H2020-TEST-001"
        assert opportunities[0].funder == "European Commission Horizon Europe"
        assert opportunities[0].closing_date is not None


# ---------------------------------------------------------------------------
# UK Space Agency - parse sections
# ---------------------------------------------------------------------------

class TestUkSpaceAgencyFundingSource:
    def test_parses_funding_sections(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <h2 id="open-call-1">Open Call for Proposals</h2>
        <p>Deadline: 15 August 2026</p>
        <p>Funding: \xc2\xa31 million available for space technology.</p>
        <a href="/funding/open-call-1">Details</a>
        <h2 id="rolling-call">Rolling Call for Proposals</h2>
        <p>Deadline: Open until further notice</p>
        <p>Funding: \xc2\xa3500,000 available for space technology.</p>
        <a href="/funding/rolling-call">Details</a>
        <h2 id="closed-call">Closed Call for Proposals</h2>
        <p>Deadline: Closed until further notice</p>
        <p>Funding: \xc2\xa350,000 available for space community activities.</p>
        <a href="/funding/closed-call">Details</a>
        <h2 id="closed">Closed Opportunities</h2>
        <p>Past calls listed here.</p>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = UkSpaceAgencyFundingSource(
            SourceSettings(
                id="uk_space",
                type="uk_space_agency_funding",
                url="https://ukspaceagency.gov.uk/funding",
            )
        )
        opportunities = source.fetch()
        titles = [o.title for o in opportunities]
        assert "Open Call for Proposals" in titles
        assert "Rolling Call for Proposals" in titles
        assert "Closed Call for Proposals" not in titles
        assert "Closed Opportunities" not in titles


# ---------------------------------------------------------------------------
# RAEng - parse programme cards
# ---------------------------------------------------------------------------

class TestRoyalAcademyEngineeringProgrammesSource:
    def test_parses_programme_cards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <div class="card__wrapper">
          <h2 class="card-title">Global Challenges Programme</h2>
          <p class="card-text">Funding for engineering solutions to global challenges.</p>
          <a href="/grants/programmes/global-challenges" class="btn">Learn more</a>
        </div>
        </div>
        </div>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = RoyalAcademyEngineeringProgrammesSource(
            SourceSettings(
                id="raeng",
                type="raeng_programmes",
                url="https://www.raeng.org.uk/grants/programmes",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Global Challenges Programme"
        assert opportunities[0].funder == "Royal Academy of Engineering"


# ---------------------------------------------------------------------------
# Royal Society - parse application date tables
# ---------------------------------------------------------------------------

class TestRoyalSocietyApplicationDatesSource:
    def test_parses_application_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <table>
        <tr>
          <td><a href="/grants/laurance-remarkable">Laurance Kirk Fellowship</a></td>
          <td>1 March 2026</td>
          <td>30 September 2026</td>
          <td>February 2027</td>
        </tr>
        </table>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = RoyalSocietyApplicationDatesSource(
            SourceSettings(
                id="royal_society",
                type="royal_society_application_dates",
                url="https://royalsociety.org/grants-and-funding/application-dates/",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Laurance Kirk Fellowship"
        assert opportunities[0].url == (
            "https://royalsociety.org/grants/laurance-remarkable"
        )
        assert opportunities[0].funder == "Royal Society"
        assert opportunities[0].opening_date is not None
        assert opportunities[0].closing_date is not None


# ---------------------------------------------------------------------------
# Academy of Medical Sciences - parse grant scheme articles
# ---------------------------------------------------------------------------

class TestAcademyMedicalSciencesGrantsSource:
    def test_parses_grant_scheme_articles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b"""
        <html><body>
        <article class="boxgrid">
          <a href="/grants/test-scheme"><h3>Test Grant Scheme</h3></a>
        </article>
        </body></html>
        """
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _DummyResponse(html),
        )
        source = AcademyMedicalSciencesGrantSchemesSource(
            SourceSettings(
                id="ams_grants",
                type="academy_medical_sciences_grants",
                url="https://acadmedsci.ac.uk/grants-and-funding/",
            )
        )
        opportunities = source.fetch()
        assert len(opportunities) == 1
        assert opportunities[0].title == "Test Grant Scheme"
        assert opportunities[0].funder == "Academy of Medical Sciences"
