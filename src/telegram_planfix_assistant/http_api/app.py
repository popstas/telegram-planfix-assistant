"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import AppConfig, load_config
from telegram_planfix_assistant.folders import FolderBackend
from telegram_planfix_assistant.groups import GroupBackend
from telegram_planfix_assistant.health import collect_health, default_database_path
from telegram_planfix_assistant.http_api.auth import BearerAuth
from telegram_planfix_assistant.http_api.folders import build_router as build_folders_router
from telegram_planfix_assistant.http_api.groups import build_router as build_groups_router
from telegram_planfix_assistant.http_api.members import build_router as build_members_router
from telegram_planfix_assistant.http_api.topics import build_router as build_topics_router
from telegram_planfix_assistant.members import MemberAddBackend
from telegram_planfix_assistant.persistence.store import OperationStore
from telegram_planfix_assistant.telegram_client.session import (
    TelethonSessionManager,
)
from telegram_planfix_assistant.topics import TopicBackend

FolderBackendFactory = Callable[[Request], FolderBackend | None]
GroupBackendFactory = Callable[[Request], GroupBackend | None]
TopicBackendFactory = Callable[[Request], TopicBackend | None]
MemberBackendFactory = Callable[[Request], MemberAddBackend | None]


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


def _default_topic_backend_factory(
    session_manager: TelethonSessionManager | None,
) -> TopicBackendFactory:
    """Build a Telethon-backed topic backend factory.

    Mirrors :func:`_default_folder_backend_factory`: returns ``None`` until a
    Telethon client is available so the HTTP layer can return 503 rather than
    silently 500.
    """

    def _factory(_request: Request) -> TopicBackend | None:
        if session_manager is None:
            return None
        client = getattr(session_manager, "_client", None)
        if client is None:
            return None
        from telegram_planfix_assistant.topics.telethon_backend import (
            TelethonTopicBackend,
        )

        return TelethonTopicBackend(client)

    return _factory


def _default_member_backend_factory(
    session_manager: TelethonSessionManager | None,
) -> MemberBackendFactory:
    """Build a Telethon-backed member backend factory."""

    def _factory(_request: Request) -> MemberAddBackend | None:
        if session_manager is None:
            return None
        client = getattr(session_manager, "_client", None)
        if client is None:
            return None
        from telegram_planfix_assistant.members.telethon_backend import (
            TelethonMemberBackend,
        )

        return TelethonMemberBackend(client)

    return _factory


def _default_group_backend_factory(
    session_manager: TelethonSessionManager | None,
) -> GroupBackendFactory:
    """Build a Telethon-backed group backend factory.

    Mirrors :func:`_default_folder_backend_factory`: returns ``None`` until a
    Telethon client is available so the HTTP layer can return 503 rather than
    silently 500.
    """

    def _factory(_request: Request) -> GroupBackend | None:
        if session_manager is None:
            return None
        client = getattr(session_manager, "_client", None)
        if client is None:
            return None
        from telegram_planfix_assistant.groups.telethon_backend import (
            TelethonGroupBackend,
        )

        return TelethonGroupBackend(client)

    return _factory


def create_app(
    config: AppConfig | None = None,
    *,
    session_manager: TelethonSessionManager | None = None,
    database_path: Path | None = None,
    folder_backend_factory: FolderBackendFactory | None = None,
    group_backend_factory: GroupBackendFactory | None = None,
    topic_backend_factory: TopicBackendFactory | None = None,
    member_backend_factory: MemberBackendFactory | None = None,
    operation_store: OperationStore | None = None,
) -> FastAPI:
    """Build a FastAPI instance.

    Loads config from `data/config.yml` when none is supplied, so this factory
    is usable both from production (`uvicorn ... --factory`) and from tests
    that inject an `AppConfig` directly. ``session_manager`` and
    ``database_path`` are optional so the service can come up — and respond
    to ``GET /health`` — before the Telethon session has been authorized.
    ``folder_backend_factory`` / ``group_backend_factory`` let tests inject
    fakes without spinning up Telethon. ``operation_store`` lets tests share a
    store between requests; in production we open one rooted at
    :func:`default_database_path` so HTTP requests can replay completed
    operations from the same SQLite file the worker writes to.
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
    app.state.group_backend_factory = (
        group_backend_factory
        if group_backend_factory is not None
        else _default_group_backend_factory(session_manager)
    )
    app.state.topic_backend_factory = (
        topic_backend_factory
        if topic_backend_factory is not None
        else _default_topic_backend_factory(session_manager)
    )
    app.state.member_backend_factory = (
        member_backend_factory
        if member_backend_factory is not None
        else _default_member_backend_factory(session_manager)
    )
    if operation_store is not None:
        app.state.operation_store = operation_store
    else:
        db_path = (
            database_path
            if database_path is not None
            else default_database_path(config)
        )
        try:
            app.state.operation_store = OperationStore(db_path)
        except Exception:
            # If the store can't be opened (e.g. read-only mount during a
            # smoke test) leave the slot empty — the groups router will return
            # 503 on demand rather than failing at app startup.
            app.state.operation_store = None

    app.include_router(_build_health_router())
    app.include_router(_build_protected_router(), prefix="/telegram")
    app.include_router(build_folders_router(), prefix="/telegram")
    app.include_router(build_groups_router(), prefix="/telegram")
    app.include_router(build_topics_router(), prefix="/telegram")
    app.include_router(build_members_router(), prefix="/telegram")

    return app
