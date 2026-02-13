from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(slots=True)
class SourceSettings:
    id: str
    type: str
    url: str
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


@dataclass(slots=True)
class PostingSettings:
    max_posts_per_run: int = 10
    dry_run: bool = False
    record_non_matches_as_seen: bool = True


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
    storage: StorageSettings = field(default_factory=StorageSettings)
    log_level: str = "INFO"


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"Expected a list of strings, got: {type(value)!r}")
    return [str(item).strip() for item in value if str(item).strip()]


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
        source_type = str(source.get("type", "")).strip()
        source_url = str(source.get("url", "")).strip()
        if not source_id or not source_type or not source_url:
            raise ConfigError(f"Source entry #{index} missing one of: id, type, url")

        options = {
            key: value
            for key, value in source.items()
            if key not in {"id", "type", "url"}
        }

        sources.append(
            SourceSettings(
                id=source_id,
                type=source_type,
                url=source_url,
                options=options,
            )
        )

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

    slack_settings = SlackSettings(
        webhook_env_var=str(raw_slack.get("webhook_env_var", "SLACK_WEBHOOK_URL")).strip()
        or "SLACK_WEBHOOK_URL"
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

    raw_storage = parsed.get("storage", {}) or {}
    if not isinstance(raw_storage, dict):
        raise ConfigError("storage must be a mapping")

    storage_path = str(raw_storage.get("path", "data/state.sqlite")).strip() or "data/state.sqlite"
    storage_settings = StorageSettings(
        type=str(raw_storage.get("type", "sqlite")).strip() or "sqlite",
        path=_resolve_relative_path(config_path, storage_path),
    )

    return AppConfig(
        sources=sources,
        filters=filter_settings,
        slack=slack_settings,
        posting=posting_settings,
        storage=storage_settings,
        log_level=str(parsed.get("log_level", "INFO")).upper(),
    )
