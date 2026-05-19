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
