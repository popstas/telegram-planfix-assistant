"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import AppConfig, load_config
from telegram_planfix_assistant.health import collect_health
from telegram_planfix_assistant.http_api.auth import BearerAuth
from telegram_planfix_assistant.telegram_client.session import (
    TelethonSessionManager,
)


def _build_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health(request: Request) -> dict[str, str]:
        report = await collect_health(
            request.app.state.config,
            session_manager=request.app.state.session_manager,
            database_path=request.app.state.database_path,
        )
        payload = report.to_dict()
        payload["version"] = __version__
        return payload

    return router


def _build_protected_router() -> APIRouter:
    """Placeholder router for endpoints requiring bearer-token auth.

    Real routes (groups, topics, members, messages, folders) are added in
    later tasks. The router is mounted here so auth is wired up from day one.
    """
    router = APIRouter(dependencies=[BearerAuth])

    @router.get("/whoami")
    async def whoami() -> dict[str, str]:
        return {"status": "authenticated"}

    return router


def create_app(
    config: AppConfig | None = None,
    *,
    session_manager: TelethonSessionManager | None = None,
    database_path: Path | None = None,
) -> FastAPI:
    """Build a FastAPI instance.

    Loads config from `data/config.yml` when none is supplied, so this factory
    is usable both from production (`uvicorn ... --factory`) and from tests
    that inject an `AppConfig` directly. ``session_manager`` and
    ``database_path`` are optional so the service can come up — and respond
    to ``GET /health`` — before the Telethon session has been authorized.
    """
    if config is None:
        config = load_config()

    app = FastAPI(
        title="telegram-planfix-assistant",
        version=__version__,
    )
    app.state.config = config
    app.state.session_manager = session_manager
    app.state.database_path = database_path

    app.include_router(_build_health_router())
    app.include_router(_build_protected_router(), prefix="/telegram")

    return app
