from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import requests

from funding_slackbot.llm import LLMError, LocalLLMClient, _digest_from_llm_content
from funding_slackbot.models import Opportunity, OpportunityMatch


def _match(external_id: str, *, summary: str = "summary text") -> OpportunityMatch:
    return OpportunityMatch(
        Opportunity(
            source_id="ukri_rss",
            external_id=external_id,
            title=f"Opportunity {external_id}",
            url=f"https://example.test/{external_id}",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            summary=summary,
            raw={},
        ),
        "keywords: AI",
    )


def _llm_content(*item_ids: str) -> str:
    return json.dumps(
        {
            "title": "Grouped funding",
            "introduction": "Related calls.",
            "groups": [
                {
                    "heading": "AI calls",
                    "summary": "Matched AI opportunities.",
                    "item_ids": list(item_ids),
                }
            ],
        }
    )


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
            raise requests.HTTPError(f"{self.status_code}: {self.text}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _client(**overrides: Any) -> LocalLLMClient:
    settings = {
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


def test_digest_from_llm_content_accepts_exact_ids() -> None:
    matches = [_match("first"), _match("second")]

    digest = _digest_from_llm_content(
        _llm_content("ukri_rss:first", "ukri_rss:second"),
        matches,
    )

    assert digest.generated_by_llm is True
    assert digest.title == "Grouped funding"
    assert [
        match.opportunity.external_id for match in digest.groups[0].items
    ] == ["first", "second"]


def test_digest_from_llm_content_rejects_inconsistent_ids() -> None:
    matches = [_match("first"), _match("second"), _match("third")]
    content = json.dumps(
        {
            "title": "Grouped funding",
            "introduction": "Related calls.",
            "groups": [
                {
                    "heading": "AI",
                    "summary": "Some matched calls.",
                    "item_ids": ["ukri_rss:first", "ukri_rss:unknown"],
                },
                {
                    "heading": "More AI",
                    "summary": "More matched calls.",
                    "item_ids": ["ukri_rss:first", "ukri_rss:second"],
                },
            ],
        }
    )

    with pytest.raises(LLMError, match="unknown.*duplicate.*missing"):
        _digest_from_llm_content(content, matches)


def test_group_opportunities_retries_transient_local_llm_status(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        _Response(503, text="busy"),
        _Response(
            200,
            payload={
                "choices": [
                    {"message": {"content": _llm_content("ukri_rss:first")}}
                ]
            },
        ),
    ]

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> _Response:
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)

    digest = _client(prompt_summary_chars=4).group_opportunities(
        [_match("first", summary="summary text")]
    )

    prompt = json.loads(calls[0]["messages"][1]["content"])
    assert len(calls) == 2
    assert prompt["opportunities"][0]["summary"] == "summ"
    assert digest.groups[0].items[0].opportunity.external_id == "first"


def test_group_opportunities_retries_without_response_format(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        _Response(400, text="response_format is not supported"),
        _Response(
            200,
            payload={
                "choices": [
                    {"message": {"content": _llm_content("ukri_rss:first")}}
                ]
            },
        ),
    ]

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> _Response:
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)

    digest = _client(retry_attempts=1).group_opportunities([_match("first")])

    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert digest.groups[0].items[0].opportunity.external_id == "first"
