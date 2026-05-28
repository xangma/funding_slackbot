from __future__ import annotations

import json
import logging
from typing import Any

import requests

from funding_slackbot.models import (
    OpportunityDigest,
    OpportunityGroup,
    OpportunityMatch,
)
from funding_slackbot.utils.datetime_utils import format_datetime, to_utc

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when a local LLM request fails or returns unusable output."""


class LocalLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: int,
        max_tokens: int,
        temperature: float,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.api_key = api_key

    def is_model_available(self) -> bool:
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=min(self.timeout_seconds, 10),
            )
            response.raise_for_status()
            models = response.json().get("data", [])
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Local LLM availability check failed: %s", exc)
            return False

        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id")
            aliases = model.get("aliases") or []
            if model_id == self.model or self.model in aliases:
                return True
        return False

    def group_opportunities(
        self,
        matches: list[OpportunityMatch],
    ) -> OpportunityDigest:
        if not matches:
            return build_simple_digest(matches, generated_by_llm=False)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _GROUPING_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "opportunities": [
                                _match_to_prompt_item(match) for match in matches
                            ]
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            requests.RequestException,
        ) as exc:
            raise LLMError(f"local LLM grouping request failed: {exc}") from exc

        return _digest_from_llm_content(content, matches)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def build_simple_digest(
    matches: list[OpportunityMatch],
    *,
    generated_by_llm: bool,
) -> OpportunityDigest:
    grouped: dict[str, list[OpportunityMatch]] = {}
    for match in matches:
        opportunity = match.opportunity
        key = opportunity.funder or opportunity.funding_type or opportunity.source_id
        grouped.setdefault(key, []).append(match)

    groups = [
        OpportunityGroup(
            heading=heading,
            summary=(
                f"{len(items)} matched "
                f"opportunit{'ies' if len(items) != 1 else 'y'}."
            ),
            items=items,
        )
        for heading, items in grouped.items()
    ]
    opportunity_word = "opportunity" if len(matches) == 1 else "opportunities"
    return OpportunityDigest(
        title="New funding opportunities",
        introduction=f"{len(matches)} new matching {opportunity_word}.",
        groups=groups,
        generated_by_llm=generated_by_llm,
    )


_GROUPING_SYSTEM_PROMPT = """You group funding opportunities for a Slack digest.
Return JSON only with this exact shape:
{
  "title": "short digest title",
  "introduction": "one sentence summary",
  "groups": [
    {
      "heading": "theme name",
      "summary": "one sentence explaining the theme",
      "item_ids": ["exact ids from the input"]
    }
  ]
}
Use every input id exactly once. Do not invent ids. Keep headings under 8 words.
Do not rewrite deadlines, URLs, funders, or money values; those are rendered by the application."""


def _match_to_prompt_item(match: OpportunityMatch) -> dict[str, str | None]:
    opportunity = match.opportunity
    return {
        "id": _match_id(match),
        "title": opportunity.title,
        "summary": opportunity.summary[:1000],
        "funder": opportunity.funder,
        "funding_type": opportunity.funding_type,
        "total_fund": opportunity.total_fund,
        "closing_date": _format_for_prompt(opportunity.closing_date),
        "match_reason": match.match_reason,
    }


def _digest_from_llm_content(
    content: str,
    matches: list[OpportunityMatch],
) -> OpportunityDigest:
    try:
        parsed = json.loads(_extract_json_object(content))
    except (TypeError, ValueError) as exc:
        raise LLMError(f"local LLM returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LLMError("local LLM JSON root was not an object")

    by_id = {_match_id(match): match for match in matches}
    used: set[str] = set()
    groups: list[OpportunityGroup] = []
    for raw_group in parsed.get("groups", []):
        if not isinstance(raw_group, dict):
            continue
        item_ids = raw_group.get("item_ids", [])
        if not isinstance(item_ids, list):
            continue
        items = [
            by_id[item_id]
            for item_id in item_ids
            if isinstance(item_id, str) and item_id in by_id and item_id not in used
        ]
        if not items:
            continue
        used.update(_match_id(item) for item in items)
        groups.append(
            OpportunityGroup(
                heading=_clean_text(raw_group.get("heading"), "Other opportunities", 80),
                summary=_clean_text(raw_group.get("summary"), "", 240),
                items=items,
            )
        )

    missing = [match for match in matches if _match_id(match) not in used]
    if missing:
        groups.append(
            OpportunityGroup(
                heading="Other opportunities",
                summary="Additional matched opportunities.",
                items=missing,
            )
        )

    if not groups and matches:
        raise LLMError("local LLM did not assign any known opportunity ids")

    return OpportunityDigest(
        title=_clean_text(parsed.get("title"), "New funding opportunities", 80),
        introduction=_clean_text(
            parsed.get("introduction"),
            f"{len(matches)} new matching opportunities.",
            280,
        ),
        groups=groups,
        generated_by_llm=True,
    )


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found")
    return stripped[start : end + 1]


def _match_id(match: OpportunityMatch) -> str:
    return f"{match.opportunity.source_id}:{match.opportunity.external_id}"


def _format_for_prompt(value: Any) -> str | None:
    if value is None:
        return None
    value_utc = to_utc(value)
    if (
        value_utc.hour == 0
        and value_utc.minute == 0
        and value_utc.second == 0
        and value_utc.microsecond == 0
    ):
        return value_utc.strftime("%Y-%m-%d")
    return format_datetime(value_utc)


def _clean_text(value: Any, fallback: str, max_length: int) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = " ".join(value.split())
    if not normalized:
        return fallback
    return normalized[:max_length]
