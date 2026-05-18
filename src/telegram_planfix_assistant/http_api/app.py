"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import AppConfig, load_config
from telegram_planfix_assistant.folders import FolderBackend
from telegram_planfix_assistant.health import collect_health
from telegram_planfix_assistant.http_api.auth import BearerAuth
from telegram_planfix_assistant.http_api.folders import build_router as build_folders_router
from telegram_planfix_assistant.telegram_client.session import (
    TelethonSessionManager,
)

FolderBackendFactory = Callable[[Request], FolderBackend | None]


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


def _default_folder_backend_factory(
    session_manager: TelethonSessionManager | None,
) -> FolderBackendFactory:
    """Build a backend factory bound to the configured Telethon session.

    The factory returns ``None`` when no session manager is wired up, which the
    folders router translates into ``503 Service Unavailable``. This keeps the
    HTTP surface honest about not being able to talk to Telegram instead of
    silently 500ing.
    """

    def _factory(_request: Request) -> FolderBackend | None:
        if session_manager is None:
            return None
        client = getattr(session_manager, "_client", None)
        if client is None:
            # The session has not been used yet this process, so we have no
            # connected client to wrap. Returning None makes the folders
            # router emit 503 rather than racing a connect() on the request
            # thread.
            return None
        from telegram_planfix_assistant.folders import TelethonFolderBackend

        return TelethonFolderBackend(client)

    return _factory


def create_app(
    config: AppConfig | None = None,
    *,
    session_manager: TelethonSessionManager | None = None,
    database_path: Path | None = None,
    folder_backend_factory: FolderBackendFactory | None = None,
) -> FastAPI:
    """Build a FastAPI instance.

    Loads config from `data/config.yml` when none is supplied, so this factory
    is usable both from production (`uvicorn ... --factory`) and from tests
    that inject an `AppConfig` directly. ``session_manager`` and
    ``database_path`` are optional so the service can come up — and respond
    to ``GET /health`` — before the Telethon session has been authorized.
    ``folder_backend_factory`` lets tests inject a fake :class:`FolderBackend`
    without spinning up Telethon; in production it defaults to one bound to
    the supplied session manager.
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
    app.state.folder_backend_factory = (
        folder_backend_factory
        if folder_backend_factory is not None
        else _default_folder_backend_factory(session_manager)
    )

    app.include_router(_build_health_router())
    app.include_router(_build_protected_router(), prefix="/telegram")
    app.include_router(build_folders_router(), prefix="/telegram")

    return app
