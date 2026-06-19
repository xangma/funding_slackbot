from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(slots=True)
class SourceSettings:
    id: str
    type: str
    url: str
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FilterSettings:
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    include_councils: list[str] = field(default_factory=list)
    include_funding_types: list[str] = field(default_factory=list)
    min_days_until_deadline: int | None = None


@dataclass(slots=True)
class SlackSettings:
    webhook_env_var: str = "SLACK_WEBHOOK_URL"
    timeout_seconds: int = 15
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0


@dataclass(slots=True)
class PostingSettings:
    max_posts_per_run: int = 10
    dry_run: bool = False
    record_non_matches_as_seen: bool = True


@dataclass(slots=True)
class LLMSettings:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "qwen3.6"
    api_key_env_var: str | None = None
    timeout_seconds: int = 60
    max_tokens: int = 1200
    temperature: float = 0.1
    retry_attempts: int = 2
    retry_backoff_seconds: float = 1.0
    prompt_summary_chars: int = 600
    group_opportunities: bool = False
    assess_opportunities: bool = False


@dataclass(slots=True)
class ReminderSettings:
    enabled: bool = False
    days_before_deadline: int = 7
    max_reminders_per_run: int = 10


@dataclass(slots=True)
class DigestSettings:
    batch_new_opportunities: bool = False
    post_at_hour: int = 9
    timezone: str = "Europe/London"
    post_when_pending_count_reaches: int = 10
    max_items_per_message: int = 25


@dataclass(slots=True)
class StorageSettings:
    type: str = "sqlite"
    path: str = "data/state.sqlite"


@dataclass(slots=True)
class AppConfig:
    sources: list[SourceSettings]
    filters: FilterSettings = field(default_factory=FilterSettings)
    slack: SlackSettings = field(default_factory=SlackSettings)
    posting: PostingSettings = field(default_factory=PostingSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    digest: DigestSettings = field(default_factory=DigestSettings)
    reminders: ReminderSettings = field(default_factory=ReminderSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    log_level: str = "INFO"


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"Expected a list of strings, got: {type(value)!r}")
    return [str(item).strip() for item in value if str(item).strip()]


def _as_optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.startswith(("http://", "https://")) and field_name.endswith(
        "api_key_env_var"
    ):
        raise ConfigError(f"{field_name} must be an environment variable name")
    return normalized


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in {0, 1}:
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False

    raise ConfigError(f"{field_name} must be a boolean")


def _as_int(value: Any, *, field_name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer")

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be an integer") from exc

    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{field_name} must be >= {minimum}")
    return parsed


def _as_hour(value: Any, *, field_name: str) -> int:
    parsed = _as_int(value, field_name=field_name, minimum=0)
    if parsed > 23:
        raise ConfigError(f"{field_name} must be <= 23")
    return parsed


def _as_float(value: Any, *, field_name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a number")

    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a number") from exc

    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{field_name} must be >= {minimum:g}")
    return parsed


def _resolve_relative_path(config_path: Path, raw_path: str) -> str:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((config_path.parent / candidate).resolve())


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle) or {}

    if not isinstance(parsed, dict):
        raise ConfigError("Config root must be a mapping")

    raw_sources = parsed.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("Config must define at least one source")

    sources: list[SourceSettings] = []
    for index, source in enumerate(raw_sources, start=1):
        if not isinstance(source, dict):
            raise ConfigError(f"Source entry #{index} must be a mapping")

        source_id = str(source.get("id", "")).strip()
        source_type = str(source.get("type", "")).strip().lower()
        source_url = str(source.get("url", "")).strip()
        if not source_id or not source_type or not source_url:
            raise ConfigError(f"Source entry #{index} missing one of: id, type, url")
        enabled = _as_bool(
            source.get("enabled", True),
            field_name=f"sources.{source_id}.enabled",
        )
        if not enabled:
            continue

        options = {
            key: value
            for key, value in source.items()
            if key not in {"id", "type", "url", "enabled"}
        }

        sources.append(
            SourceSettings(
                id=source_id,
                type=source_type,
                url=source_url,
                enabled=enabled,
                options=options,
            )
        )
    if not sources:
        raise ConfigError("Config must define at least one enabled source")

    raw_filters = parsed.get("filters", {}) or {}
    if not isinstance(raw_filters, dict):
        raise ConfigError("filters must be a mapping")

    min_days_raw = raw_filters.get("min_days_until_deadline")
    min_days = (
        _as_int(
            min_days_raw,
            field_name="filters.min_days_until_deadline",
            minimum=0,
        )
        if min_days_raw is not None
        else None
    )

    filter_settings = FilterSettings(
        include_keywords=_as_string_list(raw_filters.get("include_keywords")),
        exclude_keywords=_as_string_list(raw_filters.get("exclude_keywords")),
        include_councils=_as_string_list(raw_filters.get("include_councils")),
        include_funding_types=_as_string_list(raw_filters.get("include_funding_types")),
        min_days_until_deadline=min_days,
    )

    raw_slack = parsed.get("slack", {}) or {}
    if not isinstance(raw_slack, dict):
        raise ConfigError("slack must be a mapping")

    webhook_env_var = (
        str(raw_slack.get("webhook_env_var", "SLACK_WEBHOOK_URL")).strip()
        or "SLACK_WEBHOOK_URL"
    )
    if webhook_env_var.startswith(("http://", "https://")):
        raise ConfigError(
            "slack.webhook_env_var must be an environment variable name, not a webhook URL"
        )

    slack_settings = SlackSettings(
        webhook_env_var=webhook_env_var,
        timeout_seconds=_as_int(
            raw_slack.get("timeout_seconds", 15),
            field_name="slack.timeout_seconds",
            minimum=1,
        ),
        retry_attempts=_as_int(
            raw_slack.get("retry_attempts", 3),
            field_name="slack.retry_attempts",
            minimum=1,
        ),
        retry_backoff_seconds=_as_float(
            raw_slack.get("retry_backoff_seconds", 1.0),
            field_name="slack.retry_backoff_seconds",
            minimum=0,
        ),
    )

    raw_posting = parsed.get("posting", {}) or {}
    if not isinstance(raw_posting, dict):
        raise ConfigError("posting must be a mapping")

    posting_settings = PostingSettings(
        max_posts_per_run=_as_int(
            raw_posting.get("max_posts_per_run", 10),
            field_name="posting.max_posts_per_run",
            minimum=1,
        ),
        dry_run=_as_bool(
            raw_posting.get("dry_run", False),
            field_name="posting.dry_run",
        ),
        record_non_matches_as_seen=_as_bool(
            raw_posting.get("record_non_matches_as_seen", True),
            field_name="posting.record_non_matches_as_seen",
        ),
    )

    raw_llm = parsed.get("llm", {}) or {}
    if not isinstance(raw_llm, dict):
        raise ConfigError("llm must be a mapping")

    llm_base_url = (
        str(raw_llm.get("base_url", "http://127.0.0.1:8001/v1")).strip()
        or "http://127.0.0.1:8001/v1"
    )
    llm_model = str(raw_llm.get("model", "qwen3.6")).strip() or "qwen3.6"
    llm_settings = LLMSettings(
        enabled=_as_bool(raw_llm.get("enabled", False), field_name="llm.enabled"),
        base_url=llm_base_url.rstrip("/"),
        model=llm_model,
        api_key_env_var=_as_optional_string(
            raw_llm.get("api_key_env_var"),
            field_name="llm.api_key_env_var",
        ),
        timeout_seconds=_as_int(
            raw_llm.get("timeout_seconds", 60),
            field_name="llm.timeout_seconds",
            minimum=1,
        ),
        max_tokens=_as_int(
            raw_llm.get("max_tokens", 1200),
            field_name="llm.max_tokens",
            minimum=128,
        ),
        temperature=_as_float(
            raw_llm.get("temperature", 0.1),
            field_name="llm.temperature",
            minimum=0,
        ),
        retry_attempts=_as_int(
            raw_llm.get("retry_attempts", 2),
            field_name="llm.retry_attempts",
            minimum=1,
        ),
        retry_backoff_seconds=_as_float(
            raw_llm.get("retry_backoff_seconds", 1.0),
            field_name="llm.retry_backoff_seconds",
            minimum=0,
        ),
        prompt_summary_chars=_as_int(
            raw_llm.get("prompt_summary_chars", 600),
            field_name="llm.prompt_summary_chars",
            minimum=0,
        ),
        group_opportunities=_as_bool(
            raw_llm.get("group_opportunities", False),
            field_name="llm.group_opportunities",
        ),
        assess_opportunities=_as_bool(
            raw_llm.get("assess_opportunities", False),
            field_name="llm.assess_opportunities",
        ),
    )

    raw_digest = parsed.get("digest", {}) or {}
    if not isinstance(raw_digest, dict):
        raise ConfigError("digest must be a mapping")

    digest_timezone = (
        str(raw_digest.get("timezone", "Europe/London")).strip()
        or "Europe/London"
    )
    try:
        ZoneInfo(digest_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"digest.timezone is not a known timezone: {digest_timezone}"
        ) from exc

    digest_settings = DigestSettings(
        batch_new_opportunities=_as_bool(
            raw_digest.get("batch_new_opportunities", False),
            field_name="digest.batch_new_opportunities",
        ),
        post_at_hour=_as_hour(
            raw_digest.get("post_at_hour", 9),
            field_name="digest.post_at_hour",
        ),
        timezone=digest_timezone,
        post_when_pending_count_reaches=_as_int(
            raw_digest.get("post_when_pending_count_reaches", 10),
            field_name="digest.post_when_pending_count_reaches",
            minimum=1,
        ),
        max_items_per_message=_as_int(
            raw_digest.get("max_items_per_message", 25),
            field_name="digest.max_items_per_message",
            minimum=1,
        ),
    )

    raw_reminders = parsed.get("reminders", {}) or {}
    if not isinstance(raw_reminders, dict):
        raise ConfigError("reminders must be a mapping")

    reminder_settings = ReminderSettings(
        enabled=_as_bool(
            raw_reminders.get("enabled", False),
            field_name="reminders.enabled",
        ),
        days_before_deadline=_as_int(
            raw_reminders.get("days_before_deadline", 7),
            field_name="reminders.days_before_deadline",
            minimum=1,
        ),
        max_reminders_per_run=_as_int(
            raw_reminders.get("max_reminders_per_run", 10),
            field_name="reminders.max_reminders_per_run",
            minimum=1,
        ),
    )

    raw_storage = parsed.get("storage", {}) or {}
    if not isinstance(raw_storage, dict):
        raise ConfigError("storage must be a mapping")

    storage_path = str(raw_storage.get("path", "data/state.sqlite")).strip() or "data/state.sqlite"
    storage_type = str(raw_storage.get("type", "sqlite")).strip().lower() or "sqlite"
    if storage_type != "sqlite":
        raise ConfigError(f"Unsupported storage type: {storage_type}")

    storage_settings = StorageSettings(
        type=storage_type,
        path=_resolve_relative_path(config_path, storage_path),
    )

    log_level = str(parsed.get("log_level", "INFO")).strip().upper() or "INFO"
    valid_log_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
    if log_level not in valid_log_levels:
        raise ConfigError(
            f"log_level must be one of: {', '.join(sorted(valid_log_levels))}"
        )

    return AppConfig(
        sources=sources,
        filters=filter_settings,
        slack=slack_settings,
        posting=posting_settings,
        llm=llm_settings,
        digest=digest_settings,
        reminders=reminder_settings,
        storage=storage_settings,
        log_level=log_level,
    )
