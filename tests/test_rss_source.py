from __future__ import annotations

import json
from datetime import timezone

import pytest

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.notifiers.slack_webhook import render_slack_message_text
from funding_slackbot.sources.rss_source import (
    InnovationFundingSearchSource,
    LeverhulmeListingsSource,
    PortsmouthJobsSource,
    RssSource,
    WellcomeSchemesSource,
)
from funding_slackbot.utils.url_utils import canonicalize_url


class _DummyResponse:
    def __init__(
        self,
        content: bytes,
        url: str = "https://example.test/wrd/run/etrec179gf.open",
    ) -> None:
        self.content = content
        self.text = content.decode("utf-8")
        self.url = url

    def raise_for_status(self) -> None:
        return None


def _print_parsed_example(
    *,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
    opportunity: Opportunity,
) -> None:
    if not _is_verbose_requested(pytestconfig):
        return
    preview_reason = "parser preview (filter not evaluated)"
    with capsys.disabled():
        print(f"[parsed] {opportunity.source_id}:")
        print(render_slack_message_text(opportunity, preview_reason))
        print("")


def _is_verbose_requested(pytestconfig: pytest.Config) -> bool:
    invocation_args = pytestconfig.invocation_params.args
    for arg in invocation_args:
        if arg == "--verbose" or (arg.startswith("-") and set(arg[1:]) == {"v"}):
            return True
    return pytestconfig.getoption("verbose") > 0


def test_rss_parsing_maps_to_opportunity_and_uses_stable_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\">
      <channel>
        <title>Example</title>
        <item>
          <title>AI for health systems</title>
          <link>https://www.ukri.org/opportunity/example-opportunity/?utm_source=rss</link>
          <guid>https://www.ukri.org/opportunity/example-opportunity/?utm_source=rss</guid>
          <pubDate>Tue, 06 Jan 2026 10:00:00 +0000</pubDate>
          <description><![CDATA[<p>Closing date: 30 March 2026</p>]]></description>
        </item>
      </channel>
    </rss>
    """

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _DummyResponse(xml))

    source = RssSource(
        SourceSettings(
            id="ukri_rss",
            type="rss",
            url="https://www.ukri.org/opportunity/feed/",
        )
    )

    opportunities = source.fetch()

    assert len(opportunities) == 1
    opportunity = opportunities[0]

    assert opportunity.source_id == "ukri_rss"
    assert opportunity.title == "AI for health systems"
    assert opportunity.url == canonicalize_url(
        "https://www.ukri.org/opportunity/example-opportunity/?utm_source=rss"
    )
    assert opportunity.external_id == opportunity.url
    assert opportunity.published_at is not None
    assert opportunity.published_at.tzinfo == timezone.utc
    assert opportunity.closing_date is not None


def test_rss_falls_back_to_url_hash_when_guid_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <rss version=\"2.0\">
      <channel>
        <title>Example</title>
        <item>
          <title>Digital twin innovation</title>
          <link>https://www.ukri.org/opportunity/another-opportunity/?utm_medium=rss&utm_campaign=test</link>
          <description>Funding type: Grant</description>
        </item>
      </channel>
    </rss>
    """

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _DummyResponse(xml))

    source = RssSource(
        SourceSettings(
            id="ukri_rss",
            type="rss",
            url="https://www.ukri.org/opportunity/feed/",
        )
    )

    first_run = source.fetch()[0]
    second_run = source.fetch()[0]

    assert first_run.external_id.startswith("urlhash:")
    assert first_run.external_id == second_run.external_id


def test_wellcome_source_fetches_open_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "props": {
            "pageProps": {
                "initialListings": [
                    {
                        "id": "5596",
                        "url": "/research-funding/schemes/wellcome-career-development-awards/",
                        "title": "Wellcome Career Development Awards ",
                        "listing_summary": "<p>Funding for mid-career researchers.</p>",
                        "scheme_accepting_applications": "Open to applications",
                        "scheme_closes_for_applications": "26 March 2026",
                        "scheme_opens_for_applications": "16 October 2024",
                        "frequency": "Three times a year",
                        "level_of_funding": "<p>Up to \u00a3250,000 per year.</p>",
                    }
                ]
            }
        }
    }
    html = (
        "<html><body>"
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
        "</body></html>"
    ).encode("utf-8")

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _DummyResponse(html))

    source = WellcomeSchemesSource(
        SourceSettings(
            id="wellcome_schemes",
            type="wellcome_schemes",
            url="https://wellcome.org/research-funding/schemes",
        )
    )

    opportunities = source.fetch()

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.source_id == "wellcome_schemes"
    assert opportunity.funder == "Wellcome"
    assert opportunity.funding_type == "Three times a year"
    assert opportunity.closing_date is not None
    assert opportunity.closing_date.tzinfo == timezone.utc
    assert opportunity.url == canonicalize_url(
        "https://wellcome.org/research-funding/schemes/wellcome-career-development-awards/"
    )


def test_innovation_source_dedupes_against_ukri_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    competitions_html = b"""
    <html><body>
      <ul class="govuk-list">
        <li>
          <h2 class="govuk-heading-m">
            <a class="govuk-link" href="/competition/2397/overview/abc">
              Zero Emission Flight Demonstrator Round 1
            </a>
          </h2>
          <div class="wysiwyg-styles">Duplicate with UKRI title wording.</div>
          <dl class="date-definition-list">
            <dt>Opens:</dt><dd>16 February 2026</dd>
            <dt>Closes:</dt><dd>1 April 2026</dd>
          </dl>
        </li>
        <li>
          <h2 class="govuk-heading-m">
            <a class="govuk-link" href="/competition/2401/overview/xyz">Unique Innovation Competition</a>
          </h2>
          <div class="wysiwyg-styles">Only on innovation funding search.</div>
          <dl class="date-definition-list">
            <dt>Opened:</dt><dd>12 February 2026</dd>
            <dt>Closes:</dt><dd>20 April 2026</dd>
          </dl>
        </li>
      </ul>
    </body></html>
    """
    ukri_feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Example</title>
        <item>
          <title>Zero Emission Flight Demonstrator round one</title>
          <link>https://www.ukri.org/opportunity/zero-emission-flight-demonstrator-round-one/</link>
          <description>Innovate UK opportunity</description>
        </item>
      </channel>
    </rss>
    """

    def _fake_get(url: str, *args, **kwargs) -> _DummyResponse:
        if "ukri.org/opportunity/feed" in url:
            return _DummyResponse(ukri_feed)
        if "apply-for-innovation-funding.service.gov.uk/competition/search" in url:
            return _DummyResponse(competitions_html)
        raise AssertionError(url)

    monkeypatch.setattr("requests.get", _fake_get)

    source = InnovationFundingSearchSource(
        SourceSettings(
            id="innovation_funding_search",
            type="innovation_funding_search",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/search",
        )
    )

    opportunities = source.fetch()

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.title == "Unique Innovation Competition"
    assert opportunity.external_id == "innovation-competition:2401"
    assert opportunity.funder == "Innovate UK"
    assert opportunity.closing_date is not None
    assert opportunity.closing_date.tzinfo == timezone.utc


def test_parser_smoke_prints_one_parsed_grant_per_source(
    monkeypatch: pytest.MonkeyPatch,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ukri_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Example</title>
        <item>
          <title>AI for health systems</title>
          <link>https://www.ukri.org/opportunity/example-opportunity/?utm_source=rss</link>
          <guid>https://www.ukri.org/opportunity/example-opportunity/?utm_source=rss</guid>
          <pubDate>Tue, 06 Jan 2026 10:00:00 +0000</pubDate>
          <description><![CDATA[<p>Closing date: 30 March 2026</p>]]></description>
        </item>
      </channel>
    </rss>
    """
    wellcome_payload = {
        "props": {
            "pageProps": {
                "initialListings": [
                    {
                        "id": "5596",
                        "url": "/research-funding/schemes/wellcome-career-development-awards/",
                        "title": "Wellcome Career Development Awards ",
                        "listing_summary": "<p>Funding for mid-career researchers.</p>",
                        "scheme_accepting_applications": "Open to applications",
                        "scheme_closes_for_applications": "26 March 2026",
                    }
                ]
            }
        }
    }
    wellcome_html = (
        "<html><body>"
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(wellcome_payload)}"
        "</script>"
        "</body></html>"
    ).encode("utf-8")
    innovation_html = b"""
    <html><body>
      <ul class="govuk-list">
        <li>
          <h2 class="govuk-heading-m">
            <a class="govuk-link" href="/competition/2401/overview/xyz">Unique Innovation Competition</a>
          </h2>
          <div class="wysiwyg-styles">Only on innovation funding search.</div>
          <dl class="date-definition-list">
            <dt>Opened:</dt><dd>12 February 2026</dd>
            <dt>Closes:</dt><dd>20 April 2026</dd>
          </dl>
        </li>
      </ul>
    </body></html>
    """

    def _fake_get(url: str, *args, **kwargs) -> _DummyResponse:
        if "wellcome.org/research-funding/schemes" in url:
            return _DummyResponse(wellcome_html)
        if "apply-for-innovation-funding.service.gov.uk/competition/search" in url:
            return _DummyResponse(innovation_html)
        if "ukri.org/opportunity/feed" in url:
            return _DummyResponse(ukri_xml)
        raise AssertionError(url)

    monkeypatch.setattr("requests.get", _fake_get)

    ukri_source = RssSource(
        SourceSettings(
            id="ukri_rss",
            type="rss",
            url="https://www.ukri.org/opportunity/feed/",
        )
    )
    wellcome_source = WellcomeSchemesSource(
        SourceSettings(
            id="wellcome_schemes",
            type="wellcome_schemes",
            url="https://wellcome.org/research-funding/schemes",
        )
    )
    innovation_source = InnovationFundingSearchSource(
        SourceSettings(
            id="innovation_funding_search",
            type="innovation_funding_search",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/search",
        )
    )

    ukri = ukri_source.fetch()[0]
    wellcome = wellcome_source.fetch()[0]
    innovation = innovation_source.fetch()[0]

    _print_parsed_example(
        pytestconfig=pytestconfig,
        capsys=capsys,
        opportunity=ukri,
    )
    _print_parsed_example(
        pytestconfig=pytestconfig,
        capsys=capsys,
        opportunity=wellcome,
    )
    _print_parsed_example(
        pytestconfig=pytestconfig,
        capsys=capsys,
        opportunity=innovation,
    )

    assert ukri.title == "AI for health systems"
    assert wellcome.title == "Wellcome Career Development Awards"
    assert innovation.title == "Unique Innovation Competition"


def test_portsmouth_jobs_source_filters_related_roles(
    monkeypatch: pytest.MonkeyPatch,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html = b'<input name="WVID.STD_HID_FLDS.ET_BASE.1-1" value="217310N6lo"><input name="SESSION.STD_HID_FLDS.ET_BASE.1-1" value="SESSION123">'
    payload = {"results": [{"vacancy_id": "1", "job_title": "Research Software Engineer", "job_description": "Physics and computing support", "app_close_d": "20260220", "salary": "50000", "basis_id": "Full-Time"}, {"vacancy_id": "2", "job_title": "Muslim Chaplain", "job_description": "Pastoral support", "app_close_d": "20260220"}]}

    def _fake_get(url: str, *args, **kwargs) -> _DummyResponse:
        if "etrec179gf.open" in url:
            return _DummyResponse(html)
        if "etrec106gf.json" in url:
            return _DummyResponse(json.dumps(payload).encode("utf-8"))
        raise AssertionError(url)

    monkeypatch.setattr("requests.get", _fake_get)
    source = PortsmouthJobsSource(SourceSettings(id="portsmouth_jobs", type="portsmouth_jobs", url="https://mss.port.ac.uk/ce0732li_webrecruitment/wrd/run/etrec179gf.open?wvid=217310N6lo"))
    opportunities = source.fetch()
    _print_parsed_example(
        pytestconfig=pytestconfig,
        capsys=capsys,
        opportunity=opportunities[0],
    )
    assert [item.title for item in opportunities] == ["Research Software Engineer"]


def test_leverhulme_listings_source_falls_back_to_closing_dates(
    monkeypatch: pytest.MonkeyPatch,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    listings_html = b"<html><body><h1>Grant listings</h1></body></html>"
    closing_dates_html = b"""
    <html><body>
      <table>
        <tr><td><a href="/early-career-fellowships">Early Career Fellowships</a></td><td>19 February 2026</td></tr>
        <tr><td><a href="/research-project-grants">Research Project Grants</a></td><td><p>1 July 2026</p><p>1 July 2026</p><p>1 September 2026</p></td></tr>
      </table>
    </body></html>
    """
    calls: list[str] = []

    def _fake_get(url: str, *args, **kwargs) -> _DummyResponse:
        calls.append(url)
        if "leverhulme.ac.uk/listings" in url:
            return _DummyResponse(listings_html, url=url)
        if "leverhulme.ac.uk/closing-dates" in url:
            return _DummyResponse(closing_dates_html, url=url)
        raise AssertionError(url)

    monkeypatch.setattr("requests.get", _fake_get)

    source = LeverhulmeListingsSource(
        SourceSettings(
            id="leverhulme_listings",
            type="leverhulme_listings",
            url="https://www.leverhulme.ac.uk/listings",
        )
    )
    opportunities = source.fetch()
    _print_parsed_example(
        pytestconfig=pytestconfig,
        capsys=capsys,
        opportunity=opportunities[0],
    )

    assert any("closing-dates" in url for url in calls)
    assert len(opportunities) == 3
    assert opportunities[0].title == "Early Career Fellowships"
    assert opportunities[0].closing_date is not None
    assert sum(1 for item in opportunities if item.title == "Research Project Grants") == 2
