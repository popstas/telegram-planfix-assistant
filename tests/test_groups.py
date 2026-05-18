"""Tests for Task 7 — group creation (domain, HTTP, CLI).

The fake :class:`FakeGroupBackend` and :class:`FakeFolderBackend` keep the
tests free of Telethon. They record every call so we can assert the order in
which the domain workflow drives the backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from telegram_planfix_assistant.cli import main as cli_main
from telegram_planfix_assistant.config import load_config_from_text
from telegram_planfix_assistant.folders import (
    FolderChat,
    FolderSnapshot,
)
from telegram_planfix_assistant.groups import (
    GroupCreateFailed,
    GroupCreateNeedsReview,
    GroupCreateRequest,
    create_group,
)
from telegram_planfix_assistant.http_api import create_app
from telegram_planfix_assistant.persistence import (
    OperationStatus,
    OperationStore,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGroupBackend:
    """In-memory :class:`GroupBackend` recording every call."""

    def __init__(
        self,
        *,
        chat_id: int = -100123,
        fail_on_add: set[str] | None = None,
        fail_on_promote: set[str] | None = None,
        invite_link: str | None = "https://t.me/+invitehash",
    ) -> None:
        self._chat_id = chat_id
        self._fail_on_add = fail_on_add or set()
        self._fail_on_promote = fail_on_promote or set()
        self._invite_link = invite_link
        self.created: list[dict[str, Any]] = []
        self.added: list[str] = []
        self.promoted: list[str] = []
        self.messages: list[tuple[int, str]] = []
        self.invite_calls = 0

    async def create_supergroup(
        self,
        *,
        title: str,
        about: str | None,
        enable_topics: bool,
    ) -> int:
        self.created.append(
            {"title": title, "about": about, "enable_topics": enable_topics}
        )
        return self._chat_id

    async def add_member(self, *, chat_id: int, user: str) -> None:
        if user in self._fail_on_add:
            raise RuntimeError(f"privacy restricted: {user}")
        self.added.append(user)

    async def promote_admin(self, *, chat_id: int, user: str) -> None:
        if user in self._fail_on_promote:
            raise RuntimeError(f"cannot promote: {user}")
        self.promoted.append(user)

    async def create_invite_link(self, *, chat_id: int) -> str:
        self.invite_calls += 1
        if self._invite_link is None:
            raise RuntimeError("invite-link disabled in this test")
        return self._invite_link

    async def send_message(self, *, chat_id: int, text: str) -> int:
        self.messages.append((chat_id, text))
        return 42


class FakeFolderBackend:
    def __init__(
        self,
        folders: list[FolderSnapshot] | None = None,
        *,
        add_should_fail: bool = False,
    ) -> None:
        self._folders = folders if folders is not None else [_default_folder()]
        self._add_should_fail = add_should_fail
        self.added: list[tuple[int, int]] = []

    async def list_folders(self) -> list[FolderSnapshot]:
        return [
            FolderSnapshot(
                folder_id=f.folder_id,
                folder_name=f.folder_name,
                chats=list(f.chats),
            )
            for f in self._folders
        ]

    async def resolve_chat(self, chat_ref: str | int) -> FolderChat:
        if isinstance(chat_ref, int):
            return FolderChat(chat_id=chat_ref, title=f"Chat {chat_ref}")
        raise LookupError(f"unknown chat ref {chat_ref!r}")

    async def add_chat_to_folder(self, folder_id: int, chat_id: int) -> None:
        if self._add_should_fail:
            raise RuntimeError("telegram refused folder mutation")
        self.added.append((folder_id, chat_id))


def _default_folder() -> FolderSnapshot:
    return FolderSnapshot(
        folder_id=2,
        folder_name="Planfix clients",
        chats=[],
    )


# ---------------------------------------------------------------------------
# Domain tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> OperationStore:
    return OperationStore(tmp_path / "state.db")


def _config(minimal_config_yaml: str):
    return load_config_from_text(minimal_config_yaml)


async def test_create_group_happy_path(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    request = GroupCreateRequest(
        title="Acme",
        planfix_task_id=42,
        admins=["@alice"],
        members=["@bob"],
    )

    result, op = await create_group(
        backend=backend,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )

    assert op.status is OperationStatus.COMPLETED
    assert result.telegram_chat_id == -100123
    assert result.title == "Acme"
    assert result.planfix_task_id == 42
    assert result.topics_enabled is True
    assert result.invite_link == "https://t.me/+invitehash"
    assert result.folder_name == "Planfix clients"
    assert result.folder_id == 2
    # @bob (member), @planfix_bot (reserve_member), @alice (admin),
    # @reserve_account (reserve_admin)
    assert result.members_added == ["@bob", "@planfix_bot", "@alice", "@reserve_account"]
    assert result.admins_promoted == ["@alice", "@reserve_account"]
    assert result.task_message_sent is True
    assert backend.messages == [(-100123, "/task 42")]
    assert folder_backend.added == [(2, -100123)]
    assert result.replayed is False


async def test_create_group_skip_reserve_uses_only_explicit_lists(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    request = GroupCreateRequest(
        title="Beta",
        admins=["@alice"],
        members=["@bob"],
        skip_reserve=True,
    )

    result, _ = await create_group(
        backend=backend,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )

    assert "@planfix_bot" not in result.members_added
    assert "@reserve_account" not in result.members_added
    assert result.members_added == ["@bob", "@alice"]
    assert result.admins_promoted == ["@alice"]
    assert result.task_message_sent is False


async def test_create_group_idempotent_by_planfix_task_id(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend1 = FakeGroupBackend(chat_id=-1)
    folder_backend = FakeFolderBackend()
    request = GroupCreateRequest(
        title="Acme",
        planfix_task_id=42,
        admins=[],
        members=[],
        skip_reserve=True,
    )
    first, op1 = await create_group(
        backend=backend1,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )
    assert first.replayed is False
    assert op1.status is OperationStatus.COMPLETED

    # Re-call with a different backend instance — replay must NOT touch it.
    backend2 = FakeGroupBackend(chat_id=-2)
    second, op2 = await create_group(
        backend=backend2,
        folder_backend=FakeFolderBackend(),
        store=store,
        config=config.telegram,
        request=request,
    )
    assert second.replayed is True
    assert second.telegram_chat_id == -1
    assert op2.id == op1.id
    assert backend2.created == []


async def test_create_group_idempotent_by_title_when_no_task_id(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend1 = FakeGroupBackend(chat_id=-7)
    folder_backend = FakeFolderBackend()
    request = GroupCreateRequest(
        title="Beta",
        skip_reserve=True,
    )

    first, _ = await create_group(
        backend=backend1,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )

    backend2 = FakeGroupBackend(chat_id=-99)
    second, _ = await create_group(
        backend=backend2,
        folder_backend=FakeFolderBackend(),
        store=store,
        config=config.telegram,
        request=request,
    )
    assert second.telegram_chat_id == -7
    assert backend2.created == []
    assert second.replayed is True


async def test_create_group_missing_folder_marks_needs_review(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    # Folder backend without the configured folder present.
    folder_backend = FakeFolderBackend(folders=[])
    request = GroupCreateRequest(
        title="Acme",
        planfix_task_id=1,
        skip_reserve=True,
    )

    with pytest.raises(GroupCreateNeedsReview):
        await create_group(
            backend=backend,
            folder_backend=folder_backend,
            store=store,
            config=config.telegram,
            request=request,
        )
    # Replay should now also surface needs_review.
    with pytest.raises(GroupCreateNeedsReview):
        await create_group(
            backend=backend,
            folder_backend=folder_backend,
            store=store,
            config=config.telegram,
            request=request,
        )


async def test_create_group_folder_mutation_failure_marks_needs_review(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend(add_should_fail=True)
    request = GroupCreateRequest(
        title="Acme",
        planfix_task_id=2,
        skip_reserve=True,
    )

    with pytest.raises(GroupCreateNeedsReview):
        await create_group(
            backend=backend,
            folder_backend=folder_backend,
            store=store,
            config=config.telegram,
            request=request,
        )


async def test_create_group_task_message_only_when_bot_present(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    # planfix_task_id set, but skip_reserve removes @planfix_bot — so no task
    # message should be sent.
    request = GroupCreateRequest(
        title="Gamma",
        planfix_task_id=77,
        admins=["@alice"],
        skip_reserve=True,
    )
    result, _ = await create_group(
        backend=backend,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )
    assert result.task_message_sent is False
    assert backend.messages == []


async def test_create_group_failure_records_failed(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)

    class _BrokenBackend(FakeGroupBackend):
        async def create_supergroup(self, *, title, about, enable_topics):  # type: ignore[override]
            raise RuntimeError("server overloaded")

    backend = _BrokenBackend()
    folder_backend = FakeFolderBackend()
    request = GroupCreateRequest(title="Delta", skip_reserve=True)

    with pytest.raises(RuntimeError):
        await create_group(
            backend=backend,
            folder_backend=folder_backend,
            store=store,
            config=config.telegram,
            request=request,
        )
    with pytest.raises(GroupCreateFailed):
        await create_group(
            backend=FakeGroupBackend(),
            folder_backend=FakeFolderBackend(),
            store=store,
            config=config.telegram,
            request=request,
        )


async def test_create_group_skip_folder_bypasses_folder_resolution(
    minimal_config_yaml: str, store: OperationStore
) -> None:
    config = _config(minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend(folders=[])
    request = GroupCreateRequest(
        title="Acme",
        planfix_task_id=11,
        skip_reserve=True,
        skip_folder=True,
    )
    result, op = await create_group(
        backend=backend,
        folder_backend=folder_backend,
        store=store,
        config=config.telegram,
        request=request,
    )
    assert op.status is OperationStatus.COMPLETED
    assert result.folder_id is None
    assert folder_backend.added == []


# ---------------------------------------------------------------------------
# HTTP tests
# ---------------------------------------------------------------------------


def _http_client(
    minimal_config_yaml: str,
    backend: FakeGroupBackend,
    folder_backend: FakeFolderBackend | None = None,
    store: OperationStore | None = None,
) -> TestClient:
    config = load_config_from_text(minimal_config_yaml)
    folder_backend = folder_backend if folder_backend is not None else FakeFolderBackend()
    if store is None:
        # An in-memory-ish store is fine for HTTP tests too. We point it at a
        # tmp file via tempfile so the same store handles the entire lifetime
        # of the TestClient.
        import tempfile

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        store = OperationStore(Path(tmp.name))
    app = create_app(
        config,
        session_manager=None,
        folder_backend_factory=lambda _request: folder_backend,
        group_backend_factory=lambda _request: backend,
        operation_store=store,
    )
    return TestClient(app)


def test_http_create_group_happy_path(minimal_config_yaml: str) -> None:
    backend = FakeGroupBackend()
    client = _http_client(minimal_config_yaml, backend)
    resp = client.post(
        "/telegram/groups",
        json={
            "title": "Acme",
            "planfix_task_id": 42,
            "admins": ["@alice"],
            "members": ["@bob"],
        },
        headers={"Authorization": "Bearer secret_token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["telegram_chat_id"] == -100123
    assert body["folder_name"] == "Planfix clients"
    assert body["task_message_sent"] is True
    assert body["operation_status"] == "completed"
    assert "operation_id" in body


def test_http_create_group_replay_returns_same_chat_id(
    minimal_config_yaml: str,
) -> None:
    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    store = OperationStore(Path(tmp.name))

    backend1 = FakeGroupBackend(chat_id=-1)
    client1 = _http_client(minimal_config_yaml, backend1, store=store)
    r1 = client1.post(
        "/telegram/groups",
        json={"title": "Acme", "planfix_task_id": 42},
        headers={"Authorization": "Bearer secret_token"},
    )
    assert r1.status_code == 200, r1.text

    backend2 = FakeGroupBackend(chat_id=-2)
    client2 = _http_client(minimal_config_yaml, backend2, store=store)
    r2 = client2.post(
        "/telegram/groups",
        json={"title": "Acme", "planfix_task_id": 42},
        headers={"Authorization": "Bearer secret_token"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["telegram_chat_id"] == -1
    assert body["replayed"] is True
    # backend2 was never touched.
    assert backend2.created == []


def test_http_create_group_requires_auth(minimal_config_yaml: str) -> None:
    backend = FakeGroupBackend()
    client = _http_client(minimal_config_yaml, backend)
    resp = client.post(
        "/telegram/groups",
        json={"title": "X"},
    )
    assert resp.status_code == 401


def test_http_create_group_missing_folder_returns_502(
    minimal_config_yaml: str,
) -> None:
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend(folders=[])
    client = _http_client(minimal_config_yaml, backend, folder_backend)
    resp = client.post(
        "/telegram/groups",
        json={"title": "Lonely", "planfix_task_id": 5},
        headers={"Authorization": "Bearer secret_token"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "needs_review"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(body)
    return p


def _patch_cli_backends(
    monkeypatch: pytest.MonkeyPatch,
    backend: FakeGroupBackend,
    folder_backend: FakeFolderBackend,
    store: OperationStore,
) -> None:
    class _FakeManager:
        async def disconnect(self) -> None:
            return None

    def _factory(config_path: Path | None) -> Any:
        from telegram_planfix_assistant.config import load_config

        config = load_config(config_path)

        async def _open() -> Any:
            return backend, folder_backend

        return config, _FakeManager(), store, _open

    monkeypatch.setattr(cli_main, "_build_group_backends", _factory)


def test_cli_groups_create_happy_path(
    minimal_config_yaml: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config(tmp_path, minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    store = OperationStore(tmp_path / "state.db")
    _patch_cli_backends(monkeypatch, backend, folder_backend, store)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "groups",
            "create",
            "--title",
            "Acme",
            "--planfix-task-id",
            "42",
            "--admin",
            "@alice",
            "--member",
            "@bob",
            "--config",
            str(config_file),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["telegram_chat_id"] == -100123
    assert payload["task_message_sent"] is True
    # @planfix_bot is in the reserves so it was added via the configured default.
    assert "@planfix_bot" in payload["members_added"]


def test_cli_groups_create_no_reserve_skips_configured_reserves(
    minimal_config_yaml: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config(tmp_path, minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    store = OperationStore(tmp_path / "state.db")
    _patch_cli_backends(monkeypatch, backend, folder_backend, store)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "groups",
            "create",
            "--title",
            "Beta",
            "--no-reserve",
            "--config",
            str(config_file),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "@planfix_bot" not in payload["members_added"]
    assert "@reserve_account" not in payload["members_added"]


def test_cli_groups_create_extra_reserve_admin_adds_on_top(
    minimal_config_yaml: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config(tmp_path, minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend()
    store = OperationStore(tmp_path / "state.db")
    _patch_cli_backends(monkeypatch, backend, folder_backend, store)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "groups",
            "create",
            "--title",
            "Gamma",
            "--reserve-admin",
            "@extra_admin",
            "--config",
            str(config_file),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # Configured reserve_account is still there, and so is the explicit extra.
    assert "@reserve_account" in payload["admins_promoted"]
    assert "@extra_admin" in payload["admins_promoted"]


def test_cli_groups_create_missing_folder_exit_2(
    minimal_config_yaml: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = _write_config(tmp_path, minimal_config_yaml)
    backend = FakeGroupBackend()
    folder_backend = FakeFolderBackend(folders=[])
    store = OperationStore(tmp_path / "state.db")
    _patch_cli_backends(monkeypatch, backend, folder_backend, store)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "groups",
            "create",
            "--title",
            "Lonely",
            "--planfix-task-id",
            "5",
            "--config",
            str(config_file),
        ],
    )
    assert result.exit_code == 2
