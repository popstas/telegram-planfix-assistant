"""Unit tests for the topics-layout backend additions.

Task 2 of the topics-layout plan only adds the protocol methods and the
Telethon adapter; the service-layer set/get functions land in Task 3. These
tests therefore cover:

  * the Telethon adapter's ``set_topics_layout`` (both ``tabs`` paths and
    FLOOD_WAIT translation)
  * the Telethon adapter's ``get_topics_layout`` (boolean round-trip and the
    missing-attribute default to ``False``)
"""

from __future__ import annotations

from typing import Any

import pytest

from telegram_planfix_assistant.groups.telethon_backend import (
    TelethonGroupBackend,
)
from telegram_planfix_assistant.worker.queue import FloodWaitError


class _Peer:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id


class _RecordingClient:
    """Records ``(request_class, kwargs)`` for every ``__call__`` invocation."""

    def __init__(self, response: Any = None) -> None:
        self._response = response
        self.calls: list[Any] = []
        self.peer_lookups: list[int] = []

    async def get_input_entity(self, chat_id: int) -> _Peer:
        self.peer_lookups.append(chat_id)
        return _Peer(chat_id)

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        return self._response


@pytest.mark.asyncio
async def test_set_topics_layout_passes_tabs_true() -> None:
    client = _RecordingClient()
    backend = TelethonGroupBackend(client)

    await backend.set_topics_layout(chat_id=42, tabs=True)

    assert client.peer_lookups == [42]
    assert len(client.calls) == 1
    req = client.calls[0]
    assert req.channel.chat_id == 42
    assert req.enabled is True
    assert req.tabs is True


@pytest.mark.asyncio
async def test_set_topics_layout_passes_tabs_false() -> None:
    client = _RecordingClient()
    backend = TelethonGroupBackend(client)

    await backend.set_topics_layout(chat_id=7, tabs=False)

    assert client.calls[0].tabs is False


class _LegacyToggleClient:
    """Mimics a Telethon build whose ``ToggleForumRequest`` rejects ``tabs=``."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self._first = True

    async def get_input_entity(self, chat_id: int) -> _Peer:
        return _Peer(chat_id)

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        # First call uses tabs=...; Telethon's ToggleForumRequest constructor
        # would raise TypeError on the legacy build. Simulate that by rejecting
        # any request that carries a ``tabs`` attribute set on it.
        if self._first and hasattr(request, "tabs"):
            self._first = False
            raise TypeError("unexpected keyword argument 'tabs'")
        return None


@pytest.mark.asyncio
async def test_set_topics_layout_falls_back_when_legacy_telethon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older Telethon rejects ``tabs=...``; tabs=True should still succeed via
    the legacy two-argument signature.
    """
    # Patch ToggleForumRequest at the import site so we control its signature.
    import telethon.tl.functions.channels as channels_mod

    class _LegacyToggle:
        def __init__(self, *, channel: Any, enabled: bool, tabs: bool | None = None) -> None:
            if tabs is not None:
                # Mirror the legacy signature by refusing the kwarg.
                raise TypeError("unexpected keyword argument 'tabs'")
            self.channel = channel
            self.enabled = enabled

    monkeypatch.setattr(channels_mod, "ToggleForumRequest", _LegacyToggle)

    client = _RecordingClient()
    backend = TelethonGroupBackend(client)
    await backend.set_topics_layout(chat_id=11, tabs=True)

    assert len(client.calls) == 1
    sent = client.calls[0]
    assert isinstance(sent, _LegacyToggle)
    assert sent.enabled is True
    assert sent.channel.chat_id == 11


@pytest.mark.asyncio
async def test_set_topics_layout_legacy_rejects_list_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the build is too old to accept ``tabs=`` we cannot honour a request
    to switch *to* list — Telegram defaults new forums to list, so there's no
    legacy fallback for the demote direction. Surface a clear error rather than
    silently leaving the chat in tabs view.
    """
    import telethon.tl.functions.channels as channels_mod

    class _LegacyToggle:
        def __init__(self, *, channel: Any, enabled: bool, tabs: bool | None = None) -> None:
            if tabs is not None:
                raise TypeError("unexpected keyword argument 'tabs'")
            self.channel = channel
            self.enabled = enabled

    monkeypatch.setattr(channels_mod, "ToggleForumRequest", _LegacyToggle)

    backend = TelethonGroupBackend(_RecordingClient())
    with pytest.raises(RuntimeError, match="does not support the `tabs` argument"):
        await backend.set_topics_layout(chat_id=11, tabs=False)


class _FloodingClient:
    """Always raises a fake Telethon ``FloodWaitError`` on ``__call__``."""

    def __init__(self) -> None:
        class FloodWaitError(Exception):
            def __init__(self, seconds: int) -> None:
                super().__init__(f"FLOOD_WAIT_{seconds}")
                self.seconds = seconds

        self._fw_cls = FloodWaitError

    async def get_input_entity(self, chat_id: int) -> _Peer:
        return _Peer(chat_id)

    async def __call__(self, request: Any) -> Any:
        raise self._fw_cls(seconds=9)


@pytest.mark.asyncio
async def test_set_topics_layout_translates_flood_wait() -> None:
    backend = TelethonGroupBackend(_FloodingClient())
    with pytest.raises(FloodWaitError) as excinfo:
        await backend.set_topics_layout(chat_id=42, tabs=True)
    assert excinfo.value.seconds == 9.0


class _ResolveFloodingClient:
    """Raises FLOOD_WAIT from ``get_input_entity`` before any request runs."""

    def __init__(self) -> None:
        class FloodWaitError(Exception):
            def __init__(self, seconds: int) -> None:
                super().__init__(f"FLOOD_WAIT_{seconds}")
                self.seconds = seconds

        self._fw_cls = FloodWaitError

    async def get_input_entity(self, chat_id: int) -> Any:
        raise self._fw_cls(seconds=4)

    async def __call__(self, request: Any) -> Any:
        raise AssertionError("request should not run after resolver FLOOD_WAIT")


@pytest.mark.asyncio
async def test_set_topics_layout_translates_flood_wait_on_resolver() -> None:
    backend = TelethonGroupBackend(_ResolveFloodingClient())
    with pytest.raises(FloodWaitError) as excinfo:
        await backend.set_topics_layout(chat_id=42, tabs=True)
    assert excinfo.value.seconds == 4.0


class _FullChannelClient:
    """Returns a ``GetFullChannelRequest`` response with a configurable
    ``forum_tabs`` value (or omits the attribute entirely).
    """

    def __init__(self, *, forum_tabs: Any = "missing") -> None:
        self._forum_tabs = forum_tabs
        self.calls: list[Any] = []

    async def get_input_entity(self, chat_id: int) -> _Peer:
        return _Peer(chat_id)

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)

        if self._forum_tabs == "missing":
            full_chat = type("FC", (), {})()
        else:
            full_chat = type("FC", (), {"forum_tabs": self._forum_tabs})()
        return type("R", (), {"full_chat": full_chat})()


@pytest.mark.asyncio
async def test_get_topics_layout_returns_true_when_tabs() -> None:
    backend = TelethonGroupBackend(_FullChannelClient(forum_tabs=True))
    assert await backend.get_topics_layout(chat_id=1) is True


@pytest.mark.asyncio
async def test_get_topics_layout_returns_false_when_list() -> None:
    backend = TelethonGroupBackend(_FullChannelClient(forum_tabs=False))
    assert await backend.get_topics_layout(chat_id=1) is False


@pytest.mark.asyncio
async def test_get_topics_layout_defaults_to_false_when_attribute_missing() -> None:
    backend = TelethonGroupBackend(_FullChannelClient(forum_tabs="missing"))
    assert await backend.get_topics_layout(chat_id=1) is False


@pytest.mark.asyncio
async def test_get_topics_layout_translates_flood_wait() -> None:
    backend = TelethonGroupBackend(_FloodingClient())
    with pytest.raises(FloodWaitError) as excinfo:
        await backend.get_topics_layout(chat_id=42)
    assert excinfo.value.seconds == 9.0


@pytest.mark.asyncio
async def test_get_topics_layout_translates_flood_wait_on_resolver() -> None:
    backend = TelethonGroupBackend(_ResolveFloodingClient())
    with pytest.raises(FloodWaitError) as excinfo:
        await backend.get_topics_layout(chat_id=42)
    assert excinfo.value.seconds == 4.0
