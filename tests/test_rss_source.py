from __future__ import annotations

import json
from datetime import timezone

import pytest

from funding_slackbot.config import SourceSettings
from funding_slackbot.sources.rss_source import RssSource, WellcomeSchemesSource
from funding_slackbot.utils.url_utils import canonicalize_url


class _DummyResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.text = content.decode("utf-8")

    def raise_for_status(self) -> None:
        return None


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
