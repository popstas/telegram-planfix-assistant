"""Bulk membership-add domain shared by HTTP, CLI, and worker.

The :func:`bulk_add_members` function is the single source of truth for the
"add many users to an existing Telegram supergroup" workflow. It accepts a
list of user references (``@username``, phone string, or numeric Telegram
user id), optionally promotes them to admin per item, and runs the work
through the worker queue so ``FLOOD_WAIT`` is handled gracefully and progress
is persisted per item.

Idempotency keys (per the plan's Technical Details):

* Per item: ``MEMBER_BULK_ADD:chat=<chat_id>:user=<user>`` scoped by the
  parent operation_id via :func:`idempotency.bulk_item_key`.
* Parent: bulk parent key based on the caller-supplied ``operation_id`` (a
  fresh UUID when not supplied).

Per-item statuses returned in the aggregated response:

* ``added``         — user was added by this run (and promoted when role=admin)
* ``existed``       — replay of a previously-completed item under the same
  operation_id
* ``already_member``— Telegram reports the user is already in the chat
* ``failed``        — terminal failure (network, generic error, etc.)
* ``privacy``       — user can't be added due to their privacy settings; the
  batch keeps running when ``continue_on_error`` is true
* ``needs_review``  — undefined-outcome timeout from the underlying call
* ``skipped``       — stop_on_failure short-circuited the loop before this
  item was attempted

Privacy failures and "already member" outcomes are persisted as **completed**
items (with an inner ``status`` field in the saved result payload) because
they aren't terminal failures — Telegram reported a definitive outcome that
won't change on retry. Generic exceptions become ``failed`` items.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from telegram_planfix_assistant.persistence import idempotency
from telegram_planfix_assistant.persistence.models import (
    OperationItemRecord,
    OperationRecord,
    OperationStatus,
)
from telegram_planfix_assistant.persistence.store import OperationStore
from telegram_planfix_assistant.worker.queue import BulkItemSpec, WorkerQueue

VALID_ROLES = frozenset({"member", "admin"})


class MemberAddError(RuntimeError):
    """Base class for non-FLOOD_WAIT member-add failures surfaced by backends."""


class MemberPrivacyError(MemberAddError):
    """User cannot be added because of their privacy settings.

    Distinguished from a generic failure so the batch can keep running and
    the per-item status reflects the actual Telegram cause.
    """


class MemberAlreadyPresentError(MemberAddError):
    """Telegram reports the user is already in the chat — treated as success."""


class BulkMemberAddFailed(RuntimeError):
    """A previous bulk-add attempt with this ``operation_id`` already failed."""


class BulkMemberAddPending(RuntimeError):
    """A concurrent bulk-add attempt with this ``operation_id`` is in flight."""


class BulkMemberAddNeedsReview(RuntimeError):
    """A previous bulk-add attempt ended in ``needs_review``; operator must
    reset via ``operations retry`` to resume.
    """


# ---------------------------------------------------------------------------
# User reference normalization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedMember:
    """Canonical form of a Telegram user reference.

    ``kind`` is one of ``username`` | ``phone`` | ``user_id``. ``raw`` is the
    user-supplied input so re-runs with the same CSV stay stable even if
    formatting differs only in case.
    """

    raw: str
    kind: str
    value: str


_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_USER_ID_RE = re.compile(r"^-?\d+$")


def normalize_user_ref(raw: str) -> NormalizedMember:
    """Classify a free-text user reference into one of three accepted kinds.

    Accepted forms:

    * ``@username`` or bare ``username``
    * ``+15551234567`` or any digit string of 7–15 digits (treated as phone)
    * Numeric user id (positive integer, possibly with a leading ``-``)
    """
    if raw is None:
        raise ValueError("user reference must not be None")
    text = str(raw).strip()
    if not text:
        raise ValueError("user reference must not be empty")
    if text.startswith("@"):
        bare = text[1:].strip()
        if not bare:
            raise ValueError(f"invalid username reference: {raw!r}")
        return NormalizedMember(raw=text, kind="username", value=f"@{bare}")
    if text.startswith("+") and _PHONE_RE.match(text):
        return NormalizedMember(raw=text, kind="phone", value=text)
    if _USER_ID_RE.match(text):
        return NormalizedMember(raw=text, kind="user_id", value=text)
    return NormalizedMember(raw=text, kind="username", value=f"@{text}")


# ---------------------------------------------------------------------------
# Domain DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BulkMemberItem:
    """One row inside a bulk member-add request.

    ``role`` is ``"member"`` (default) or ``"admin"``. Anything else raises
    ``ValueError`` during construction.
    """

    user: str
    role: str = "member"

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(
                f"invalid role {self.role!r}; expected one of {sorted(VALID_ROLES)}"
            )

    def to_payload(self) -> dict[str, Any]:
        return {"user": self.user, "role": self.role}


@dataclass(frozen=True)
class BulkMemberAddRequest:
    """Input to :func:`bulk_add_members`."""

    telegram_chat_id: int
    items: Sequence[BulkMemberItem]
    continue_on_error: bool = True
    operation_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "telegram_chat_id": self.telegram_chat_id,
            "items": [it.to_payload() for it in self.items],
            "continue_on_error": self.continue_on_error,
            "operation_id": self.operation_id,
        }


@dataclass(frozen=True)
class BulkMemberItemResult:
    """One row in the aggregated response."""

    status: str
    user: str
    role: str
    normalized_user: str | None = None
    user_kind: str | None = None
    promoted: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "user": self.user,
            "role": self.role,
            "normalized_user": self.normalized_user,
            "user_kind": self.user_kind,
            "promoted": self.promoted,
            "error": self.error,
        }


@dataclass(frozen=True)
class BulkMemberAddResult:
    """Aggregated outcome of :func:`bulk_add_members`."""

    operation_id: str
    telegram_chat_id: int
    added: int
    existed: int
    already_member: int
    privacy: int
    failed: int
    needs_review: int
    skipped: int
    items: list[BulkMemberItemResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "telegram_chat_id": self.telegram_chat_id,
            "added": self.added,
            "existed": self.existed,
            "already_member": self.already_member,
            "privacy": self.privacy,
            "failed": self.failed,
            "needs_review": self.needs_review,
            "skipped": self.skipped,
            "items": [it.to_dict() for it in self.items],
        }


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class MemberAddBackend(Protocol):
    """Telethon-facing operations needed to add members to a supergroup.

    Tests inject a fake; production wires this to a Telethon adapter.

    ``add_member`` must raise :class:`MemberPrivacyError` when the user blocks
    invites via privacy settings and :class:`MemberAlreadyPresentError` when
    the user is already in the chat. Any other ``Exception`` is treated as a
    terminal failure for that item.
    """

    async def add_member(self, *, chat_id: int, user: str) -> None:
        ...

    async def promote_admin(self, *, chat_id: int, user: str) -> None:
        ...


# ---------------------------------------------------------------------------
# Bulk add
# ---------------------------------------------------------------------------


def _bulk_parent_key(operation_id: str | None) -> tuple[str, str]:
    if operation_id is not None and str(operation_id).strip():
        oid = str(operation_id).strip()
    else:
        oid = uuid.uuid4().hex
    return f"{idempotency.MEMBER_BULK_ADD}:id={oid}", oid


def _inner_status_from_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "added"
    return str(payload.get("status") or "added")


def _item_status_from_record(
    record: OperationItemRecord | None,
    *,
    was_existing: bool,
) -> str:
    if record is None:
        return "skipped"
    if record.status is OperationStatus.COMPLETED:
        inner = _inner_status_from_payload(record.result_payload)
        if inner == "privacy":
            # Privacy is sticky across replays — always report it as such.
            return "privacy"
        if inner == "already_member":
            return "existed" if was_existing else "already_member"
        return "existed" if was_existing else "added"
    if record.status is OperationStatus.FAILED:
        return "failed"
    if record.status is OperationStatus.NEEDS_REVIEW:
        return "needs_review"
    return "skipped"


def _build_item_result(
    *,
    item_input: BulkMemberItem,
    normalized: NormalizedMember,
    record: OperationItemRecord | None,
    was_existing: bool,
) -> BulkMemberItemResult:
    status = _item_status_from_record(record, was_existing=was_existing)
    if record is None:
        return BulkMemberItemResult(
            status=status,
            user=item_input.user,
            role=item_input.role,
            normalized_user=normalized.value,
            user_kind=normalized.kind,
        )
    if record.status is OperationStatus.COMPLETED:
        payload = record.result_payload or {}
        return BulkMemberItemResult(
            status=status,
            user=item_input.user,
            role=item_input.role,
            normalized_user=normalized.value,
            user_kind=normalized.kind,
            promoted=bool(payload.get("promoted", False)),
            error=payload.get("error_reason"),
        )
    return BulkMemberItemResult(
        status=status,
        user=item_input.user,
        role=item_input.role,
        normalized_user=normalized.value,
        user_kind=normalized.kind,
        error=record.error,
    )


async def bulk_add_members(
    *,
    backend: MemberAddBackend,
    store: OperationStore,
    queue: WorkerQueue,
    request: BulkMemberAddRequest,
) -> tuple[BulkMemberAddResult, OperationRecord]:
    """Add many members to a supergroup under one parent operation.

    Per-item idempotency key: ``MEMBER_BULK_ADD:chat=<chat_id>:user=<user>``
    scoped by the parent ``operation_id`` so the same bulk run never adds the
    same user twice and a restart resumes where it left off.

    Privacy-restricted users are recorded with status ``privacy`` and a
    captured reason — the batch keeps running when ``continue_on_error`` is
    true. Already-present users are treated as success
    (``status=already_member``). Generic exceptions become per-item
    ``failed`` rows.
    """
    if not request.items:
        raise ValueError("bulk member add requires at least one item")
    normalized_per_item: list[NormalizedMember] = [
        normalize_user_ref(it.user) for it in request.items
    ]

    parent_key, _ = _bulk_parent_key(request.operation_id)
    begin = store.begin_operation(
        operation_type=idempotency.MEMBER_BULK_ADD,
        idempotency_key=parent_key,
        request_payload=request.to_payload(),
    )
    parent_op = begin.operation

    specs: list[BulkItemSpec] = []
    for it, normalized in zip(request.items, normalized_per_item, strict=True):
        per_key = idempotency.member_add_key(
            telegram_chat_id=request.telegram_chat_id,
            user=normalized.value,
        )
        bulk_key = idempotency.bulk_item_key(
            operation_id=parent_op.id, per_item_key=per_key
        )
        specs.append(
            BulkItemSpec(
                idempotency_key=bulk_key,
                payload={
                    "telegram_chat_id": request.telegram_chat_id,
                    "user": normalized.value,
                    "raw_user": it.user,
                    "user_kind": normalized.kind,
                    "role": it.role,
                },
            )
        )

    if not begin.created:
        if parent_op.status is OperationStatus.COMPLETED:
            return (
                _replay_bulk(
                    parent_op=parent_op,
                    specs=specs,
                    normalized_per_item=normalized_per_item,
                    request=request,
                    store=store,
                ),
                parent_op,
            )
        if parent_op.status is OperationStatus.FAILED:
            raise BulkMemberAddFailed(
                parent_op.error or "previous bulk failed"
            )
        if parent_op.status is OperationStatus.NEEDS_REVIEW:
            raise BulkMemberAddNeedsReview(
                parent_op.error or "previous bulk needs review"
            )
        # PENDING → fall through and resume work below.

    pre_existing_completed = {
        it.idempotency_key
        for it in store.list_items(parent_op.id)
        if it.status is OperationStatus.COMPLETED
    }

    async def _handler(spec: BulkItemSpec) -> dict[str, Any]:
        payload = spec.payload
        chat_id = int(payload["telegram_chat_id"])
        user_ref = str(payload["user"])
        role = str(payload.get("role") or "member")

        try:
            await backend.add_member(chat_id=chat_id, user=user_ref)
            already_member = False
        except MemberAlreadyPresentError:
            already_member = True
        except MemberPrivacyError as exc:
            # Surface as completed with status=privacy so the queue's success
            # path persists the reason. The parent aggregator treats it as a
            # non-fatal outcome that does NOT need_review the run.
            return {
                "status": "privacy",
                "user": user_ref,
                "raw_user": payload.get("raw_user"),
                "user_kind": payload.get("user_kind"),
                "role": role,
                "promoted": False,
                "error_reason": str(exc),
            }

        promoted = False
        if role == "admin":
            await backend.promote_admin(chat_id=chat_id, user=user_ref)
            promoted = True

        return {
            "status": "already_member" if already_member else "added",
            "user": user_ref,
            "raw_user": payload.get("raw_user"),
            "user_kind": payload.get("user_kind"),
            "role": role,
            "promoted": promoted,
        }

    item_records = await queue.run_bulk(
        parent_op.id,
        specs,
        _handler,
        stop_on_failure=not request.continue_on_error,
    )

    records_by_key: dict[str, OperationItemRecord] = {
        rec.idempotency_key: rec for rec in item_records
    }
    item_results: list[BulkMemberItemResult] = []
    counts = {
        "added": 0,
        "existed": 0,
        "already_member": 0,
        "privacy": 0,
        "failed": 0,
        "needs_review": 0,
        "skipped": 0,
    }
    for spec, item_input, normalized in zip(
        specs, request.items, normalized_per_item, strict=True
    ):
        rec = records_by_key.get(spec.idempotency_key)
        was_existing = spec.idempotency_key in pre_existing_completed
        built = _build_item_result(
            item_input=item_input,
            normalized=normalized,
            record=rec,
            was_existing=was_existing,
        )
        item_results.append(built)
        counts[built.status] = counts.get(built.status, 0) + 1

    parent_payload = {
        "telegram_chat_id": request.telegram_chat_id,
        "counts": counts,
        "operation_id": parent_op.id,
    }
    # Parent transitions to completed unless something genuinely needs
    # operator attention. Privacy is a definitive Telegram outcome (the user
    # blocked invites) and doesn't require manual follow-up, so it does not
    # trigger needs_review on its own.
    if (
        counts["failed"] == 0
        and counts["needs_review"] == 0
        and counts["skipped"] == 0
    ):
        final_parent = store.complete_operation(parent_op.id, parent_payload)
    else:
        final_parent = store.mark_needs_review(
            parent_op.id,
            f"bulk_member_add finished with "
            f"failed={counts['failed']}, "
            f"needs_review={counts['needs_review']}, "
            f"skipped={counts['skipped']}",
        )

    aggregated = BulkMemberAddResult(
        operation_id=parent_op.id,
        telegram_chat_id=request.telegram_chat_id,
        added=counts["added"],
        existed=counts["existed"],
        already_member=counts["already_member"],
        privacy=counts["privacy"],
        failed=counts["failed"],
        needs_review=counts["needs_review"],
        skipped=counts["skipped"],
        items=item_results,
    )
    return aggregated, final_parent


def _replay_bulk(
    *,
    parent_op: OperationRecord,
    specs: Sequence[BulkItemSpec],
    normalized_per_item: Sequence[NormalizedMember],
    request: BulkMemberAddRequest,
    store: OperationStore,
) -> BulkMemberAddResult:
    items_by_key = {
        rec.idempotency_key: rec for rec in store.list_items(parent_op.id)
    }
    item_results: list[BulkMemberItemResult] = []
    counts = {
        "added": 0,
        "existed": 0,
        "already_member": 0,
        "privacy": 0,
        "failed": 0,
        "needs_review": 0,
        "skipped": 0,
    }
    for spec, item_input, normalized in zip(
        specs, request.items, normalized_per_item, strict=True
    ):
        rec = items_by_key.get(spec.idempotency_key)
        built = _build_item_result(
            item_input=item_input,
            normalized=normalized,
            record=rec,
            was_existing=True,
        )
        item_results.append(built)
        counts[built.status] = counts.get(built.status, 0) + 1
    return BulkMemberAddResult(
        operation_id=parent_op.id,
        telegram_chat_id=request.telegram_chat_id,
        added=counts["added"],
        existed=counts["existed"],
        already_member=counts["already_member"],
        privacy=counts["privacy"],
        failed=counts["failed"],
        needs_review=counts["needs_review"],
        skipped=counts["skipped"],
        items=item_results,
    )
