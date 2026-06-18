from __future__ import annotations

import html as html_lib
import re
import time
from typing import Any

import requests

from funding_slackbot.config import ConfigError, SourceSettings

USER_AGENT = "funding-slackbot/0.1 (+https://github.com/)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

_BREAK_TAGS = re.compile(r"</?(?:br|p|li|div|tr|h\d|ul|ol|table)[^>]*>", re.IGNORECASE)
_HTML_TAGS = re.compile(r"<[^>]+>")
_MULTISPACE = re.compile(r"\s+")


def default_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }


def http_options(settings: SourceSettings) -> tuple[int, int, float]:
    return (
        positive_int_option(
            settings.options.get("timeout_seconds", 30),
            field_name=f"sources.{settings.id}.timeout_seconds",
        ),
        positive_int_option(
            settings.options.get("retry_attempts", 3),
            field_name=f"sources.{settings.id}.retry_attempts",
        ),
        non_negative_float_option(
            settings.options.get("retry_backoff_seconds", 1.0),
            field_name=f"sources.{settings.id}.retry_backoff_seconds",
        ),
    )


def get_with_retries(
    url: str,
    *,
    timeout_seconds: int,
    headers: dict[str, str],
    max_attempts: int,
    retry_backoff_seconds: float,
    accept_retry_statuses: set[int] | None = None,
) -> requests.Response:
    retry_statuses = {202, 408, 429, 500, 502, 503, 504}
    accepted_retry_statuses = accept_retry_statuses or set()
    last_exception: requests.RequestException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, timeout=timeout_seconds, headers=headers)
        except requests.RequestException as exc:
            last_exception = exc
            if attempt == max_attempts:
                raise
            _sleep_before_retry(retry_backoff_seconds, attempt, None)
            continue

        status_code = getattr(response, "status_code", 200)
        if status_code not in retry_statuses:
            return response
        if attempt == max_attempts:
            if status_code in accepted_retry_statuses:
                return response
            raise requests.HTTPError(
                f"retryable HTTP status {status_code} from {url} "
                f"after {max_attempts} attempts",
                response=response,
            )

        _sleep_before_retry(retry_backoff_seconds, attempt, response)

    if last_exception is not None:
        raise last_exception
    raise RuntimeError("unreachable retry state")


def positive_int_option(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be an integer") from exc
    if parsed < 1:
        raise ConfigError(f"{field_name} must be >= 1")
    return parsed


def non_negative_float_option(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a number") from exc
    if parsed < 0:
        raise ConfigError(f"{field_name} must be >= 0")
    return parsed


def html_to_text(value: str) -> str:
    with_breaks = _BREAK_TAGS.sub("\n", value)
    without_tags = _HTML_TAGS.sub(" ", with_breaks)
    unescaped = html_lib.unescape(without_tags)

    cleaned_lines = []
    for line in unescaped.splitlines():
        normalized = normalize_whitespace(line)
        if normalized:
            cleaned_lines.append(normalized)
    return "\n".join(cleaned_lines)


def normalize_whitespace(value: str) -> str:
    return _MULTISPACE.sub(" ", value).strip()


def to_serializable_dict(value: Any) -> dict[str, Any]:
    serialized = _to_serializable(value)
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}


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


def _to_serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_serializable(item) for item in value]
    if isinstance(value, time.struct_time):
        return list(value)
    return str(value)
