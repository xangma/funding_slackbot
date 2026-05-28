from __future__ import annotations

import pytest

from funding_slackbot.config import ConfigError, load_config


def _write_config(tmp_path, content: str):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_config_parses_string_booleans_explicitly(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: ukri_rss
            type: RSS
            url: https://www.ukri.org/opportunity/feed/
        posting:
          dry_run: "false"
          record_non_matches_as_seen: "true"
        """,
    )

    config = load_config(path)

    assert config.sources[0].type == "rss"
    assert config.posting.dry_run is False
    assert config.posting.record_non_matches_as_seen is True


def test_load_config_rejects_webhook_url_in_env_var_field(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: ukri_rss
            type: rss
            url: https://www.ukri.org/opportunity/feed/
        slack:
          webhook_env_var: https://hooks.slack.com/services/test
        """,
    )

    with pytest.raises(ConfigError, match="environment variable name"):
        load_config(path)


def test_load_config_rejects_invalid_posting_limit(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: ukri_rss
            type: rss
            url: https://www.ukri.org/opportunity/feed/
        posting:
          max_posts_per_run: 0
        """,
    )

    with pytest.raises(ConfigError, match="max_posts_per_run"):
        load_config(path)


def test_load_config_skips_disabled_sources(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: wellcome_schemes
            type: wellcome_schemes
            url: https://wellcome.org/research-funding/schemes
            enabled: false
          - id: ukri_rss
            type: rss
            url: https://www.ukri.org/opportunity/feed/
        """,
    )

    config = load_config(path)

    assert [source.id for source in config.sources] == ["ukri_rss"]


def test_load_config_rejects_all_sources_disabled(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: wellcome_schemes
            type: wellcome_schemes
            url: https://wellcome.org/research-funding/schemes
            enabled: false
        """,
    )

    with pytest.raises(ConfigError, match="enabled source"):
        load_config(path)


def test_load_config_parses_llm_grouping_and_reminders(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: ukri_rss
            type: rss
            url: https://www.ukri.org/opportunity/feed/
        llm:
          enabled: true
          base_url: http://100.123.170.91:8001/v1/
          model: qwen3.6
          group_opportunities: true
          max_tokens: 512
        digest:
          batch_new_opportunities: true
          post_at_hour: 9
          timezone: Europe/London
          post_when_pending_count_reaches: 4
        reminders:
          enabled: true
          days_before_deadline: 7
          max_reminders_per_run: 3
        """,
    )

    config = load_config(path)

    assert config.llm.enabled is True
    assert config.llm.group_opportunities is True
    assert config.llm.base_url == "http://100.123.170.91:8001/v1"
    assert config.llm.max_tokens == 512
    assert config.digest.batch_new_opportunities is True
    assert config.digest.post_at_hour == 9
    assert config.digest.timezone == "Europe/London"
    assert config.digest.post_when_pending_count_reaches == 4
    assert config.reminders.enabled is True
    assert config.reminders.days_before_deadline == 7
    assert config.reminders.max_reminders_per_run == 3


def test_load_config_rejects_invalid_digest_timezone(tmp_path) -> None:
    path = _write_config(
        tmp_path,
        """
        sources:
          - id: ukri_rss
            type: rss
            url: https://www.ukri.org/opportunity/feed/
        digest:
          timezone: Not/AZone
        """,
    )

    with pytest.raises(ConfigError, match="digest.timezone"):
        load_config(path)
