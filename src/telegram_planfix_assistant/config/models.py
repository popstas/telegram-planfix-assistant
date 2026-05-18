"""Pydantic models describing the on-disk config (`data/config.yml`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DefaultChatFolderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_name: str = Field(..., min_length=1)
    folder_id: int | None = None


class TelegramDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_topics: bool = True
    create_invite_link: bool = True


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_id: int
    api_hash: str = Field(..., min_length=1)
    session_path: str = Field(..., min_length=1)
    main_account_label: str = Field(default="planfix-assistant-main", min_length=1)
    reserve_admins: list[str] = Field(default_factory=list)
    reserve_members: list[str] = Field(default_factory=list)
    default_chat_folder: DefaultChatFolderConfig
    defaults: TelegramDefaults = Field(default_factory=TelegramDefaults)


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = 8085
    bearer_token: str = Field(..., min_length=1)

    @field_validator("port")
    @classmethod
    def _port_in_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("http.port must be between 1 and 65535")
        return v


class QueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_parallel_telegram_ops: int = Field(default=1, ge=1)
    default_retry_delay_seconds: int = Field(default=30, ge=0)
    flood_wait_safety_margin_seconds: int = Field(default=5, ge=0)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def _level_known(cls, v: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        up = v.upper()
        if up not in allowed:
            raise ValueError(f"logging.level must be one of {sorted(allowed)}")
        return up


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram: TelegramConfig
    http: HttpConfig
    queue: QueueConfig = Field(default_factory=QueueConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
