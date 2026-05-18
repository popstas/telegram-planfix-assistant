"""Topic-creation domain shared by HTTP, CLI, and worker.

The :func:`create_topic` function is the single source of truth for the
"create a single forum topic in an existing supergroup" workflow. It handles
topic creation, first-message logic, and idempotency.

Idempotency keys (per the plan's Technical Details):

* ``planfix_task_id`` when present
* ``telegram_chat_id + topic_name`` otherwise

First-message logic, in order:

* ``/task {planfix_task_id}`` when ``planfix_task_id`` is set
* otherwise ``message`` if provided
* otherwise duplicate the topic name
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from telegram_planfix_assistant.persistence import idempotency
from telegram_planfix_assistant.persistence.models import (
    OperationRecord,
    OperationStatus,
)
from telegram_planfix_assistant.persistence.store import OperationStore


class TopicError(RuntimeError):
    """Base class for topic-creation failures surfaced to callers."""


class TopicCreateFailed(TopicError):
    """A previous attempt with this idempotency key already failed."""


class TopicCreatePending(TopicError):
    """A concurrent attempt with this idempotency key is still in flight."""


class TopicCreateNeedsReview(TopicError):
    """A previous attempt resulted in ``needs_review`` and must not auto-retry."""


@dataclass(frozen=True)
class TopicCreateRequest:
    """Input shape shared by HTTP, CLI, and tests."""

    telegram_chat_id: int
    topic_name: str
    planfix_task_id: int | str | None = None
    message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "telegram_chat_id": self.telegram_chat_id,
            "topic_name": self.topic_name,
            "planfix_task_id": self.planfix_task_id,
            "message": self.message,
        }


@dataclass(frozen=True)
class TopicCreateResult:
    """Result returned by both the live execution and a replay."""

    telegram_chat_id: int
    telegram_topic_id: int
    topic_name: str
    planfix_task_id: int | str | None
    first_message_kind: str
    first_message_text: str | None
    telegram_message_id: int | None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_topic_id": self.telegram_topic_id,
            "topic_name": self.topic_name,
            "planfix_task_id": self.planfix_task_id,
            "first_message_kind": self.first_message_kind,
            "first_message_text": self.first_message_text,
            "telegram_message_id": self.telegram_message_id,
            "replayed": self.replayed,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TopicCreateResult:
        return cls(
            telegram_chat_id=int(payload["telegram_chat_id"]),
            telegram_topic_id=int(payload["telegram_topic_id"]),
            topic_name=str(payload["topic_name"]),
            planfix_task_id=payload.get("planfix_task_id"),
            first_message_kind=str(payload.get("first_message_kind", "")),
            first_message_text=payload.get("first_message_text"),
            telegram_message_id=(
                int(payload["telegram_message_id"])
                if payload.get("telegram_message_id") is not None
                else None
            ),
            replayed=True,
        )


class TopicBackend(Protocol):
    """Telethon-facing operations needed to create a forum topic.

    Tests inject a fake; production wires this to a Telethon adapter.
    """

    async def create_topic(self, *, chat_id: int, name: str) -> int:
        ...

    async def send_message(
        self, *, chat_id: int, text: str, topic_id: int | None = None
    ) -> int:
        ...


def _first_message(
    *,
    request: TopicCreateRequest,
) -> tuple[str, str]:
    """Decide which first message to send and return ``(kind, text)``.

    Returns one of three branches:

    * ``("task", "/task <id>")`` when ``planfix_task_id`` is set
    * ``("message", <message>)`` when an explicit ``message`` is provided
    * ``("topic_name", <topic_name>)`` otherwise
    """
    if request.planfix_task_id is not None:
        return "task", f"/task {request.planfix_task_id}"
    if request.message is not None and request.message.strip():
        return "message", request.message
    return "topic_name", request.topic_name


async def _execute_create(
    *,
    backend: TopicBackend,
    request: TopicCreateRequest,
) -> TopicCreateResult:
    topic_id = await backend.create_topic(
        chat_id=request.telegram_chat_id, name=request.topic_name
    )

    kind, text = _first_message(request=request)
    message_id: int | None = None
    try:
        message_id = await backend.send_message(
            chat_id=request.telegram_chat_id,
            text=text,
            topic_id=topic_id,
        )
    except Exception:
        # First-message failure is non-fatal — the topic itself was created.
        # The caller can resend manually. We record the attempt without a
        # message id so the result still reflects which branch ran.
        message_id = None

    return TopicCreateResult(
        telegram_chat_id=request.telegram_chat_id,
        telegram_topic_id=topic_id,
        topic_name=request.topic_name,
        planfix_task_id=request.planfix_task_id,
        first_message_kind=kind,
        first_message_text=text,
        telegram_message_id=message_id,
    )


async def create_topic(
    *,
    backend: TopicBackend,
    store: OperationStore,
    request: TopicCreateRequest,
) -> tuple[TopicCreateResult, OperationRecord]:
    """Create a forum topic, or replay the saved result for the same key.

    State machine transitions identical to :func:`create_group`:

    * `completed`     → return saved result with ``replayed=True``
    * `failed`        → raise :class:`TopicCreateFailed`
    * `needs_review`  → raise :class:`TopicCreateNeedsReview`
    * `pending`       → raise :class:`TopicCreatePending`
    """
    if not request.topic_name.strip():
        raise ValueError("topic create requires non-empty topic_name")

    key = idempotency.topic_create_key(
        planfix_task_id=request.planfix_task_id,
        telegram_chat_id=request.telegram_chat_id,
        topic_name=request.topic_name,
    )
    begin = store.begin_operation(
        operation_type=idempotency.TOPIC_CREATE,
        idempotency_key=key,
        request_payload=request.to_payload(),
    )

    if not begin.created:
        op = begin.operation
        if op.status is OperationStatus.COMPLETED:
            payload = op.result_payload or {}
            return TopicCreateResult.from_dict(payload), op
        if op.status is OperationStatus.FAILED:
            raise TopicCreateFailed(op.error or "previous attempt failed")
        if op.status is OperationStatus.NEEDS_REVIEW:
            raise TopicCreateNeedsReview(
                op.error or "previous attempt needs review"
            )
        raise TopicCreatePending(
            f"operation {op.id} is still pending; retry via 'operations retry'"
        )

    operation_id = begin.operation.id
    try:
        result = await _execute_create(backend=backend, request=request)
    except TopicError:
        raise
    except Exception as exc:
        store.fail_operation(operation_id, str(exc))
        raise

    op = store.complete_operation(operation_id, result.to_dict())
    return result, op
