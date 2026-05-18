"""Tests for the YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_planfix_assistant.config import (
    AppConfig,
    ConfigError,
    load_config,
    load_config_from_text,
)


def test_load_valid_config_from_text(minimal_config_yaml: str) -> None:
    config = load_config_from_text(minimal_config_yaml)
    assert isinstance(config, AppConfig)
    assert config.telegram.api_id == 123456
    assert config.telegram.api_hash == "telegram_api_hash"
    assert config.telegram.default_chat_folder.folder_name == "Planfix clients"
    assert config.telegram.default_chat_folder.folder_id == 2
    assert config.telegram.defaults.enable_topics is True
    assert config.telegram.defaults.create_invite_link is True
    assert config.telegram.reserve_admins == ["@reserve_account"]
    assert config.telegram.reserve_members == ["@planfix_bot"]
    assert config.http.host == "0.0.0.0"
    assert config.http.port == 8085
    assert config.http.bearer_token == "secret_token"
    assert config.queue.max_parallel_telegram_ops == 1
    assert config.queue.default_retry_delay_seconds == 30
    assert config.queue.flood_wait_safety_margin_seconds == 5
    assert config.logging.level == "INFO"


def test_load_valid_config_from_file(tmp_path: Path, minimal_config_yaml: str) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(minimal_config_yaml, encoding="utf-8")

    config = load_config(config_path)
    assert config.http.bearer_token == "secret_token"


def test_defaults_applied_when_optional_sections_omitted() -> None:
    minimal = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
"""
    config = load_config_from_text(minimal)
    assert config.telegram.defaults.enable_topics is True
    assert config.telegram.defaults.create_invite_link is True
    assert config.telegram.reserve_admins == []
    assert config.telegram.reserve_members == []
    assert config.http.host == "0.0.0.0"
    assert config.http.port == 8085
    assert config.queue.max_parallel_telegram_ops == 1
    assert config.logging.level == "INFO"


def test_missing_required_telegram_keys_raises_config_error() -> None:
    bad = """
telegram:
  api_id: 1
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
"""
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_text(bad)
    msg = str(excinfo.value)
    assert "telegram.api_hash" in msg
    assert "telegram.session_path" in msg


def test_missing_http_bearer_token_raises_config_error() -> None:
    bad = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http: {}
"""
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_text(bad)
    assert "http.bearer_token" in str(excinfo.value)


def test_unknown_top_level_key_rejected() -> None:
    bad = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
oops: true
"""
    with pytest.raises(ConfigError):
        load_config_from_text(bad)


def test_alerts_defaults_applied() -> None:
    minimal = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
"""
    config = load_config_from_text(minimal)
    assert config.alerts.webhook_url is None
    assert config.alerts.flood_wait_repeat_threshold == 3
    assert config.alerts.stuck_bulk_after_seconds == 600
    assert config.alerts.error_rate_window == 20
    assert config.alerts.error_rate_threshold == 0.5


def test_alerts_section_parsed_when_present() -> None:
    src = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
alerts:
  webhook_url: "https://example.test/alert"
  flood_wait_repeat_threshold: 5
  stuck_bulk_after_seconds: 30
  error_rate_window: 10
  error_rate_threshold: 0.25
"""
    config = load_config_from_text(src)
    assert config.alerts.webhook_url == "https://example.test/alert"
    assert config.alerts.flood_wait_repeat_threshold == 5
    assert config.alerts.stuck_bulk_after_seconds == 30
    assert config.alerts.error_rate_window == 10
    assert config.alerts.error_rate_threshold == 0.25


def test_alerts_invalid_threshold_rejected() -> None:
    bad = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
alerts:
  error_rate_threshold: 1.5
"""
    with pytest.raises(ConfigError):
        load_config_from_text(bad)


def test_invalid_logging_level_rejected() -> None:
    bad = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
logging:
  level: SHOUTING
"""
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_text(bad)
    assert "logging.level" in str(excinfo.value)


def test_invalid_http_port_rejected() -> None:
    bad = """
telegram:
  api_id: 1
  api_hash: "h"
  session_path: /tmp/s
  default_chat_folder:
    folder_name: "X"
http:
  bearer_token: "tok"
  port: 0
"""
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_text(bad)
    assert "http.port" in str(excinfo.value)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yml"
    with pytest.raises(ConfigError) as excinfo:
        load_config(missing)
    assert "not found" in str(excinfo.value)


def test_empty_file_raises_config_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(empty)


def test_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("telegram: [unbalanced", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(bad)
    assert "Failed to parse YAML" in str(excinfo.value)


def test_non_mapping_top_level_rejected(tmp_path: Path) -> None:
    f = tmp_path / "list.yml"
    f.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(f)
    assert "mapping" in str(excinfo.value)
