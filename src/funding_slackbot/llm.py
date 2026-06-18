from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from funding_slackbot.models import (
    OpportunityDigest,
    OpportunityGroup,
    OpportunityMatch,
    Opportunity,
)
from funding_slackbot.utils.datetime_utils import format_datetime, to_utc

logger = logging.getLogger(__name__)

_RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when a local LLM request fails or returns unusable output."""


@dataclass(slots=True)
class OpportunityAssessment:
    matched: bool
    reason: str
    summary: str = ""
    requirements: list[str] = field(default_factory=list)
    considerations: list[str] = field(default_factory=list)


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
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 1.0,
        prompt_summary_chars: int = 600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.api_key = api_key
        self.retry_attempts = max(1, retry_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.prompt_summary_chars = max(0, prompt_summary_chars)

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
                                _match_to_prompt_item(
                                    match,
                                    summary_max_chars=self.prompt_summary_chars,
                                )
                                for match in matches
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
            data = self._chat_completion(payload)
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

    def assess_opportunity(
        self,
        opportunity: Opportunity,
        *,
        criteria: dict[str, object] | None = None,
    ) -> OpportunityAssessment | None:
        """Ask the local LLM to classify a single opportunity.

        Returns an assessment with matched=True/False and a reason string.
        Returns None only if the LLM response could not be parsed (caller
        should fall back to rule-based filtering).
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": _ASSESSMENT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        _opportunity_to_assessment_item(
                            opportunity,
                            criteria=criteria or {},
                            summary_max_chars=self.prompt_summary_chars,
                        ),
                        ensure_ascii=True,
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            data = self._chat_completion(payload)
            content = data["choices"][0]["message"]["content"]
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            requests.RequestException,
        ) as exc:
            raise LLMError(f"local LLM assessment request failed: {exc}") from exc

        return _filter_result_from_assessment(content)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._chat_completion_with_retries(
            payload,
            allow_response_format_fallback=True,
        )

    def _chat_completion_with_retries(
        self,
        payload: dict[str, Any],
        *,
        allow_response_format_fallback: bool,
    ) -> dict[str, Any]:
        last_exception: requests.RequestException | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exception = exc
                if attempt == self.retry_attempts:
                    raise
                logger.warning(
                    "Local LLM request failed on attempt %d/%d: %s",
                    attempt,
                    self.retry_attempts,
                    exc,
                )
                _sleep_before_retry(self.retry_backoff_seconds, attempt, None)
                continue

            if (
                response.status_code == 400
                and allow_response_format_fallback
                and "response_format" in payload
                and "response_format" in response.text.lower()
            ):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)
                logger.warning(
                    "Local LLM rejected response_format; retrying without it"
                )
                return self._chat_completion_with_retries(
                    fallback_payload,
                    allow_response_format_fallback=False,
                )

            if (
                response.status_code in _RETRY_STATUS_CODES
                and attempt < self.retry_attempts
            ):
                logger.warning(
                    "Local LLM returned HTTP %d on attempt %d/%d; retrying",
                    response.status_code,
                    attempt,
                    self.retry_attempts,
                )
                _sleep_before_retry(self.retry_backoff_seconds, attempt, response)
                continue

            response.raise_for_status()
            return response.json()

        if last_exception is not None:
            raise last_exception
        raise LLMError("local LLM retry loop ended without a response")


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
      "summary": "one sentence explaining why these items are grouped",
      "item_ids": ["exact ids from the input"]
    }
  ]
}
Use every input id exactly once. Do not invent ids. Keep headings under 8 words.
Omit groups that would contain no ids.
Use match reasons, assessment summaries, requirements, and considerations when choosing groups.
Do not rewrite deadlines, URLs, funders, or money values; those are rendered by the application."""

_ASSESSMENT_SYSTEM_PROMPT = """You assess whether a funding opportunity matches configured relevance criteria.
Return JSON only with this exact shape:
{
  "matched": true or false,
  "reason": "one short sentence explaining the decision",
  "summary": "one concise Slack-ready summary of the opportunity",
  "requirements": ["relevant eligibility requirements or application constraints"],
  "considerations": ["important caveats, fit notes, or follow-up checks"]
}
The user message contains criteria and one opportunity.
Use include_keywords as semantic interests, not just literal string matches.
Treat exclude_keywords as disqualifying themes.
If include_councils or include_funding_types are present, require compatibility.
If min_days_until_deadline is present, reject missing or too-soon deadlines.
Keep lists short. Include only facts supported by the opportunity.
Prefer including borderline but plausible opportunities; do not invent facts."""


def _opportunity_to_assessment_item(
    opportunity: Opportunity,
    *,
    criteria: dict[str, object],
    summary_max_chars: int,
) -> dict[str, object]:
    return {
        "criteria": criteria,
        "opportunity": {
            "id": _match_id_from_parts(opportunity.source_id, opportunity.external_id),
            "title": opportunity.title,
            "summary": opportunity.summary[:summary_max_chars],
            "funder": opportunity.funder,
            "funding_type": opportunity.funding_type,
            "total_fund": opportunity.total_fund,
            "opening_date": _format_for_prompt(opportunity.opening_date),
            "closing_date": _format_for_prompt(opportunity.closing_date),
            "published_at": _format_for_prompt(opportunity.published_at),
        },
    }


def _filter_result_from_assessment(content: str) -> OpportunityAssessment | None:
    try:
        parsed = json.loads(_extract_json_object(content))
    except (TypeError, ValueError):
        return None

    if not isinstance(parsed, dict):
        return None

    matched = parsed.get("matched")
    if not isinstance(matched, bool):
        return None

    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "LLM assessment"

    return OpportunityAssessment(
        matched=matched,
        reason=reason.strip(),
        summary=_clean_text(parsed.get("summary"), "", 600),
        requirements=_clean_text_list(parsed.get("requirements"), 5, 180),
        considerations=_clean_text_list(parsed.get("considerations"), 5, 180),
    )


def _match_to_prompt_item(
    match: OpportunityMatch,
    *,
    summary_max_chars: int,
) -> dict[str, object]:
    opportunity = match.opportunity
    return {
        "id": _match_id(match),
        "title": opportunity.title,
        "summary": opportunity.summary[:summary_max_chars],
        "funder": opportunity.funder,
        "funding_type": opportunity.funding_type,
        "total_fund": opportunity.total_fund,
        "opening_date": _format_for_prompt(opportunity.opening_date),
        "closing_date": _format_for_prompt(opportunity.closing_date),
        "published_at": _format_for_prompt(opportunity.published_at),
        "match_reason": match.match_reason,
        "assessment_summary": match.assessment_summary,
        "requirements": match.requirements,
        "considerations": match.considerations,
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

    raw_groups = parsed.get("groups")
    if not isinstance(raw_groups, list):
        raise LLMError("local LLM JSON field 'groups' was not a list")

    by_id = {_match_id(match): match for match in matches}
    used: set[str] = set()
    unknown_ids: list[str] = []
    duplicate_ids: list[str] = []
    groups: list[OpportunityGroup] = []
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            raise LLMError(f"local LLM group #{index} was not an object")
        item_ids = raw_group.get("item_ids", [])
        if not isinstance(item_ids, list) or not all(
            isinstance(item_id, str) for item_id in item_ids
        ):
            raise LLMError(
                f"local LLM group #{index} field 'item_ids' was not a string list"
            )

        items: list[OpportunityMatch] = []
        for item_id in item_ids:
            if item_id not in by_id:
                unknown_ids.append(item_id)
                continue
            if item_id in used:
                duplicate_ids.append(item_id)
                continue
            used.add(item_id)
            items.append(by_id[item_id])

        if not items:
            raise LLMError(f"local LLM group #{index} did not include usable ids")
        groups.append(
            OpportunityGroup(
                heading=_clean_text(
                    raw_group.get("heading"),
                    "Other opportunities",
                    80,
                ),
                summary=_clean_text(raw_group.get("summary"), "", 240),
                items=items,
            )
        )

    missing_ids = [
        _match_id(match) for match in matches if _match_id(match) not in used
    ]
    if unknown_ids or duplicate_ids or missing_ids:
        raise LLMError(
            "local LLM returned inconsistent grouping ids: "
            f"{_id_error_summary(unknown_ids, duplicate_ids, missing_ids)}"
        )

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


def _id_error_summary(
    unknown_ids: list[str],
    duplicate_ids: list[str],
    missing_ids: list[str],
) -> str:
    parts = [
        _format_id_error("unknown", unknown_ids),
        _format_id_error("duplicate", duplicate_ids),
        _format_id_error("missing", missing_ids),
    ]
    return "; ".join(part for part in parts if part)


def _format_id_error(label: str, ids: list[str]) -> str | None:
    if not ids:
        return None
    preview = ", ".join(ids[:3])
    if len(ids) > 3:
        preview = f"{preview}, +{len(ids) - 3} more"
    return f"{len(ids)} {label}: {preview}"


def _match_id(match: OpportunityMatch) -> str:
    return _match_id_from_parts(
        match.opportunity.source_id,
        match.opportunity.external_id,
    )


def _match_id_from_parts(source_id: str, external_id: str) -> str:
    return f"{source_id}:{external_id}"


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


def _sleep_before_retry(
    retry_backoff_seconds: float,
    attempt: int,
    response: requests.Response | None,
) -> None:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            delay = max(0.0, float(retry_after))
        except ValueError:
            delay = retry_backoff_seconds * (2 ** (attempt - 1))
    else:
        delay = retry_backoff_seconds * (2 ** (attempt - 1))
    if delay > 0:
        time.sleep(delay)


def _clean_text(value: Any, fallback: str, max_length: int) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = " ".join(value.split())
    if not normalized:
        return fallback
    return normalized[:max_length]


def _clean_text_list(value: Any, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []

    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item, "", max_length)
        if text:
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned
