"""YAML config loader with friendly error messages."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from telegram_planfix_assistant.config.models import AppConfig

DEFAULT_CONFIG_PATH = Path("data/config.yml")


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


def _format_validation_error(exc: ValidationError, source: str) -> str:
    lines = [f"Invalid configuration in {source}:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config_from_text(text: str, *, source: str = "<string>") -> AppConfig:
    """Parse and validate config from a YAML string."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML in {source}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Config file {source} is empty")
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {source} must be a YAML mapping at the top level")

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, source)) from exc


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate the application config from disk."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found at {config_path}. "
            "Create it (see docs/plans/ for the expected shape)."
        )
    if not config_path.is_file():
        raise ConfigError(f"Config path {config_path} is not a regular file")

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    return load_config_from_text(text, source=str(config_path))
