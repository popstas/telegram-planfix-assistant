"""HTTP routes for supergroup creation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from telegram_planfix_assistant.folders import FolderBackend, FolderError
from telegram_planfix_assistant.groups import (
    GroupBackend,
    GroupCreateFailed,
    GroupCreateNeedsReview,
    GroupCreatePending,
    GroupCreateRequest,
    create_group,
)
from telegram_planfix_assistant.http_api.auth import BearerAuth
from telegram_planfix_assistant.persistence.store import OperationStore


class GroupCreateBody(BaseModel):
    title: str = Field(..., min_length=1)
    planfix_task_id: int | str | None = None
    about: str | None = None
    admins: list[str] = Field(default_factory=list)
    members: list[str] = Field(default_factory=list)
    reserve_admins: list[str] | None = None
    reserve_members: list[str] | None = None
    skip_reserve: bool = False
    enable_topics: bool | None = None
    create_invite_link: bool | None = None
    folder_name: str | None = None
    folder_id: int | None = None
    skip_folder: bool = False


def _backends_or_503(
    request: Request,
) -> tuple[GroupBackend, FolderBackend | None]:
    factory = getattr(request.app.state, "group_backend_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram group backend is not configured (session may be unauthorized)",
        )
    backend = factory(request)
    if backend is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram group backend is not available",
        )
    folder_factory = getattr(request.app.state, "folder_backend_factory", None)
    folder_backend = folder_factory(request) if folder_factory is not None else None
    return backend, folder_backend


def _store_or_503(request: Request) -> OperationStore:
    store = getattr(request.app.state, "operation_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operation store is not configured",
        )
    return store


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[BearerAuth])

    @router.post("/groups")
    async def create(
        body: GroupCreateBody, request: Request
    ) -> dict[str, Any]:
        config = request.app.state.config
        backend, folder_backend = _backends_or_503(request)
        store = _store_or_503(request)

        domain_request = GroupCreateRequest(
            title=body.title,
            planfix_task_id=body.planfix_task_id,
            about=body.about,
            admins=body.admins,
            members=body.members,
            reserve_admins=body.reserve_admins,
            reserve_members=body.reserve_members,
            skip_reserve=body.skip_reserve,
            enable_topics=body.enable_topics,
            create_invite_link=body.create_invite_link,
            folder_name=body.folder_name,
            folder_id=body.folder_id,
            skip_folder=body.skip_folder,
        )

        try:
            result, op = await create_group(
                backend=backend,
                folder_backend=folder_backend,
                store=store,
                config=config.telegram,
                request=domain_request,
            )
        except GroupCreatePending as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "pending", "message": str(exc)},
            ) from exc
        except GroupCreateNeedsReview as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "needs_review", "message": str(exc)},
            ) from exc
        except GroupCreateFailed as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "previous_attempt_failed", "message": str(exc)},
            ) from exc
        except FolderError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "folder_error", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        payload = result.to_dict()
        payload["operation_id"] = op.id
        payload["operation_status"] = op.status.value
        return payload

    return router
