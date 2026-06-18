from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from funding_slackbot.config import FilterSettings
from funding_slackbot.filters import LLMAssessmentFilter
from funding_slackbot.llm import LLMError, LocalLLMClient, OpportunityAssessment
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
        closing_date=None,
        opening_date=None,
        funder="MRC",
        funding_type="Grant",
        total_fund=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _client(**overrides: Any) -> LocalLLMClient:
    settings: dict[str, Any] = {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "qwen3.6",
        "timeout_seconds": 60,
        "max_tokens": 1200,
        "temperature": 0.1,
        "retry_attempts": 2,
        "retry_backoff_seconds": 0.0,
        "prompt_summary_chars": 600,
    }
    settings.update(overrides)
    return LocalLLMClient(**settings)


# --- assess_opportunity on LocalLLMClient ---


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        payload: dict[str, Any] | None = None,
        text: str = "ok",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"{self.status_code}: {self.text}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _assessment_response(
    matched: bool,
    reason: str,
    *,
    summary: str = "",
    requirements: list[str] | None = None,
    considerations: list[str] | None = None,
) -> _Response:
    return _Response(
        200,
        payload={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "matched": matched,
                                "reason": reason,
                                "summary": summary,
                                "requirements": requirements or [],
                                "considerations": considerations or [],
                            }
                        )
                    }
                }
            ]
        },
    )


def test_assess_opportunity_returns_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **kw: _assessment_response(True, "Relevant AI grant"),
    )

    client = _client()
    result = client.assess_opportunity(_opportunity())

    assert isinstance(result, OpportunityAssessment)
    assert result.matched is True
    assert "Relevant AI grant" in result.reason


def test_assess_opportunity_returns_display_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **kw: _assessment_response(
            True,
            "Relevant AI grant",
            summary="Supports applied AI projects.",
            requirements=["UK lead organisation", "Partner required"],
            considerations=["Check internal deadline"],
        ),
    )

    result = _client().assess_opportunity(_opportunity())

    assert isinstance(result, OpportunityAssessment)
    assert result.summary == "Supports applied AI projects."
    assert result.requirements == ["UK lead organisation", "Partner required"]
    assert result.considerations == ["Check internal deadline"]


def test_assess_opportunity_returns_no_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **kw: _assessment_response(False, "Not relevant to team"),
    )

    client = _client()
    result = client.assess_opportunity(_opportunity())

    assert isinstance(result, OpportunityAssessment)
    assert result.matched is False


def test_assess_opportunity_sends_criteria_and_truncates_summary(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> _Response:
        calls.append(json)
        return _assessment_response(True, "Relevant to configured AI interests")

    monkeypatch.setattr("requests.post", fake_post)

    client = _client(prompt_summary_chars=4)
    result = client.assess_opportunity(
        _opportunity(summary="abcdef"),
        criteria={
            "include_keywords": ["AI", "digital health"],
            "exclude_keywords": ["studentship"],
            "include_councils": ["MRC"],
            "include_funding_types": ["grant"],
            "min_days_until_deadline": 10,
        },
    )

    prompt = json.loads(calls[0]["messages"][1]["content"])
    system_prompt = calls[0]["messages"][0]["content"]
    assert result is not None
    assert "Do not reject missing deadlines by itself" in system_prompt
    assert "Require positive evidence" in system_prompt
    assert "absence of exclusions" in system_prompt
    assert prompt["criteria"]["include_keywords"] == ["AI", "digital health"]
    assert prompt["criteria"]["exclude_keywords"] == ["studentship"]
    assert prompt["criteria"]["min_days_until_deadline"] == 10
    assert prompt["opportunity"]["id"] == "ukri_rss:id-1"
    assert prompt["opportunity"]["summary"] == "abcd"
    assert "opening_date" in prompt["opportunity"]
    assert "published_at" in prompt["opportunity"]


def test_assess_opportunity_raises_on_llm_error(monkeypatch) -> None:
    import requests as req
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(req.ConnectionError("refused")),
    )

    client = _client(retry_attempts=1)
    with pytest.raises(LLMError, match="assessment request failed"):
        client.assess_opportunity(_opportunity())


# --- LLMAssessmentFilter ---


def test_llm_filter_uses_llm_when_enabled(monkeypatch) -> None:
    calls = {"llm": False}

    def fake_assess(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object],
    ) -> OpportunityAssessment:
        calls["llm"] = True
        assert criteria["include_keywords"] == ["AI"]
        return OpportunityAssessment(
            matched=True,
            reason="LLM says yes",
            summary="Assessment summary",
            requirements=["Eligible org"],
            considerations=["Check date"],
        )

    monkeypatch.setattr(LocalLLMClient, "assess_opportunity", fake_assess)

    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    assert calls["llm"] is True
    assert result.matched is True
    assert "LLM says yes" in result.reason_text()
    assert result.assessment_summary == "Assessment summary"
    assert result.requirements == ["Eligible org"]
    assert result.considerations == ["Check date"]


def test_llm_filter_omits_inactive_criteria(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_assess(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object],
    ) -> OpportunityAssessment:
        captured.update(criteria)
        return OpportunityAssessment(matched=True, reason="LLM says yes")

    monkeypatch.setattr(LocalLLMClient, "assess_opportunity", fake_assess)

    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is True
    assert captured == {"include_keywords": ["AI"]}


def test_llm_filter_falls_back_to_rules_on_error(monkeypatch) -> None:
    def fake_assess(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object],
    ) -> OpportunityAssessment | None:
        raise LLMError("model unreachable")

    monkeypatch.setattr(LocalLLMClient, "assess_opportunity", fake_assess)

    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    # Should fall back to rule-based (which matches "AI" in title)
    assert result.matched is True
    assert "keywords:" in result.reason_text()


def test_llm_filter_falls_back_to_rules_on_generic_exception(monkeypatch) -> None:
    def fake_assess(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object],
    ) -> OpportunityAssessment | None:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(LocalLLMClient, "assess_opportunity", fake_assess)

    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is True
    assert "keywords:" in result.reason_text()


def test_llm_filter_uses_rules_when_llm_disabled() -> None:
    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=False,
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is True
    assert "keywords:" in result.reason_text()


def test_llm_filter_uses_rules_when_no_client() -> None:
    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=None,
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is True
    assert "keywords:" in result.reason_text()


def test_llm_filter_delegates_to_rules_for_non_match(monkeypatch) -> None:
    """When LLM says not matched, that result is used directly."""

    def fake_assess(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object],
    ) -> OpportunityAssessment:
        return OpportunityAssessment(matched=False, reason="LLM: not relevant")

    monkeypatch.setattr(LocalLLMClient, "assess_opportunity", fake_assess)

    filt = LLMAssessmentFilter(
        FilterSettings(include_keywords=["AI"]),
        llm_client=_client(),
        llm_assessment_enabled=True,
    )

    result = filt.evaluate(_opportunity())

    assert result.matched is False
    assert "LLM: not relevant" in result.reason_text()
