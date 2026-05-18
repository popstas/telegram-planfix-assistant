"""FastAPI application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import AppConfig, load_config
from telegram_planfix_assistant.http_api.auth import BearerAuth


def _build_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        # Real probes land in Task 3; this skeleton returns a static OK so the
        # service can come up before Telegram is reachable.
        return {
            "status": "ok",
            "version": __version__,
            "telegram_session": "unknown",
            "database": "unknown",
            "default_folder": "unknown",
        }

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


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build a FastAPI instance.

    Loads config from `data/config.yml` when none is supplied, so this factory
    is usable both from production (`uvicorn ... --factory`) and from tests
    that inject an `AppConfig` directly.
    """
    if config is None:
        config = load_config()

    app = FastAPI(
        title="telegram-planfix-assistant",
        version=__version__,
    )
    app.state.config = config

    app.include_router(_build_health_router())
    app.include_router(_build_protected_router(), prefix="/telegram")

    return app
