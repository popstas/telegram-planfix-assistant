"""Service-wide health probes used by both `GET /health` and `health` CLI."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from telegram_planfix_assistant.config.models import AppConfig
from telegram_planfix_assistant.telegram_client.session import (
    TelethonSessionManager,
)

SessionProbe = Callable[[], Awaitable[str]]
DatabaseProbe = Callable[[], Awaitable[str]]
FolderProbe = Callable[[], Awaitable[str]]

SESSION_AUTHORIZED = "authorized"
SESSION_UNAUTHORIZED = "unauthorized"
DATABASE_OK = "ok"
DATABASE_ERROR = "error"
FOLDER_OK = "ok"
FOLDER_MISSING = "missing"


@dataclass(frozen=True)
class HealthReport:
    """Result of a single `/health` probe sweep."""

    status: str
    telegram_session: str
    database: str
    default_folder: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def default_database_path(config: AppConfig) -> Path:
    """Database file path derived from the Telethon session location."""
    session = Path(config.telegram.session_path).expanduser()
    return session.parent / "state.db"


async def probe_telegram_session(manager: TelethonSessionManager | None) -> str:
    """Return ``authorized``/``unauthorized`` without raising."""
    if manager is None:
        return SESSION_UNAUTHORIZED
    try:
        state = await manager.state()
    except Exception:
        return SESSION_UNAUTHORIZED
    return SESSION_AUTHORIZED if state.authorized else SESSION_UNAUTHORIZED


async def probe_database(database_path: Path) -> str:
    """Verify the SQLite database file is reachable and queryable."""
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(database_path)) as conn:
            conn.execute("SELECT 1").fetchone()
        return DATABASE_OK
    except Exception:
        return DATABASE_ERROR


async def probe_default_folder(
    manager: TelethonSessionManager | None,
    folder_name: str,
) -> str:
    """Best-effort folder probe.

    The full folder resolver lands in Task 6; until then the probe can only
    distinguish ``missing`` (no usable Telethon session) from ``ok`` (session
    is authorized so the folder lookup is at least possible). The probe never
    auto-creates a folder.
    """
    del folder_name  # consumed by the real resolver in Task 6
    if manager is None:
        return FOLDER_MISSING
    try:
        state = await manager.state()
    except Exception:
        return FOLDER_MISSING
    return FOLDER_OK if state.authorized else FOLDER_MISSING


async def collect_health(
    config: AppConfig,
    *,
    session_manager: TelethonSessionManager | None = None,
    database_path: Path | None = None,
    session_probe: SessionProbe | None = None,
    database_probe: DatabaseProbe | None = None,
    folder_probe: FolderProbe | None = None,
) -> HealthReport:
    """Run the three probes concurrently-safe and bundle their results.

    `status` is always ``ok`` so the service stays reachable during startup
    even when Telegram is unauthorized — individual fields carry the detail.
    """
    db_path = database_path if database_path is not None else default_database_path(config)
    folder_name = config.telegram.default_chat_folder.folder_name

    session_status = await (
        session_probe() if session_probe is not None else probe_telegram_session(session_manager)
    )
    database_status = await (
        database_probe() if database_probe is not None else probe_database(db_path)
    )
    folder_status = await (
        folder_probe()
        if folder_probe is not None
        else probe_default_folder(session_manager, folder_name)
    )

    return HealthReport(
        status="ok",
        telegram_session=session_status,
        database=database_status,
        default_folder=folder_status,
    )
