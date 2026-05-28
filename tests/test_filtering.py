from __future__ import annotations

from datetime import datetime, timedelta, timezone

from funding_slackbot.config import FilterSettings
from funding_slackbot.filters import RuleBasedFilter
from funding_slackbot.models import Opportunity


def _opportunity(**overrides: object) -> Opportunity:
    base = Opportunity(
        source_id="ukri_rss",
        external_id="id-1",
        title="AI for NHS diagnostics",
        url="https://www.ukri.org/opportunity/test",
        published_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        summary="Machine learning platform for hospital operations",
        raw={},
        closing_date=datetime.now(timezone.utc) + timedelta(days=40),
        opening_date=None,
        funder="MRC",
        funding_type="Grant",
        total_fund=None,
    )

    for key, value in overrides.items():
        setattr(base, key, value)

    return base


def test_filter_matches_include_keywords_with_reason() -> None:
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["AI", "digital twin"],
            exclude_keywords=["studentship"],
        )
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is True
    assert "keywords:" in result.reason_text()
    assert "AI" in result.reason_text()


def test_filter_excludes_when_exclude_keyword_present() -> None:
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["AI"],
            exclude_keywords=["PhD", "studentship"],
        )
    )

    result = filt.evaluate(_opportunity(title="AI PhD studentship", summary="training grant"))

    assert result.matched is False
    assert "excluded by keyword" in result.reason_text()


def test_filter_rejects_deadline_too_close() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["AI"],
            min_days_until_deadline=10,
        ),
        now_provider=lambda: now,
    )

    result = filt.evaluate(
        _opportunity(closing_date=now + timedelta(days=2))
    )

    assert result.matched is False
    assert "deadline too soon" in result.reason_text()


def test_filter_deadline_uses_injected_clock() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["AI"],
            min_days_until_deadline=10,
        ),
        now_provider=lambda: now,
    )

    result = filt.evaluate(_opportunity(closing_date=now + timedelta(days=11)))

    assert result.matched is True
    assert "deadline in 11 days" in result.reason_text()


def test_filter_keyword_match_uses_word_boundaries() -> None:
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["AI"],
        )
    )

    result = filt.evaluate(
        _opportunity(
            title="Research software maintenance fund",
            summary="Supports maintenance and sustainability work",
        )
    )

    assert result.matched is False


def test_filter_keyword_globs_match_configured_word_families() -> None:
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["genom*", "*omics"],
        )
    )

    result = filt.evaluate(
        _opportunity(
            title="Genomics and transcriptomics platform",
            summary="Shared sequencing infrastructure",
        )
    )

    assert result.matched is True
    assert "genom*" in result.reason_text()
    assert "*omics" in result.reason_text()


def test_filter_keyword_globs_keep_outer_word_boundaries() -> None:
    filt = RuleBasedFilter(
        FilterSettings(
            include_keywords=["genom*"],
        )
    )

    result = filt.evaluate(
        _opportunity(
            title="Pangenomic research infrastructure",
            summary="Shared sequencing infrastructure",
        )
    )

    assert result.matched is False
