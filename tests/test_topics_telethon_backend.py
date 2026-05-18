"""Unit tests for the Telethon adapter that creates / lists / closes topics.

Telethon shipped the forum-topic requests under two different module paths
across versions and renamed the chat-entity kwarg from ``channel`` to
``peer``. The adapter routes around both via ``_import_forum_request`` and
``_peer_kwarg`` — these tests cover the version-detection paths so the live
e2e script doesn't have to be the first place those shims fail.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from telegram_planfix_assistant.topics import telethon_backend as tb
from telegram_planfix_assistant.topics.telethon_backend import (
    TelethonTopicBackend,
    _extract_topic_id,
    _peer_kwarg,
)


class _Peer:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id


class _RecordingClient:
    """Telethon client double that captures the request objects sent through it."""

    def __init__(self, response: Any = None) -> None:
        self._response = response
        self.calls: list[Any] = []
        self._peer_lookups: list[int] = []

    async def get_input_entity(self, chat_id: int) -> _Peer:
        self._peer_lookups.append(chat_id)
        return _Peer(chat_id)

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        return self._response

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> Any:
        self.calls.append(("send_message", chat_id, text, kwargs))

        class _Sent:
            id = 4242

        return _Sent()


class _PeerRequest:
    """Telethon-1.43+ style request that takes ``peer`` + ``random_id``."""

    def __init__(self, *, peer: Any, title: str, random_id: int) -> None:
        self.peer = peer
        self.title = title
        self.random_id = random_id


class _ChannelRequest:
    """Legacy Telethon style: ``channel`` kwarg, no ``random_id``."""

    def __init__(self, *, channel: Any, title: str) -> None:
        self.channel = channel
        self.title = title


def test_peer_kwarg_picks_peer_when_request_has_peer() -> None:
    assert _peer_kwarg(_PeerRequest, "peer-obj") == {"peer": "peer-obj"}


def test_peer_kwarg_picks_channel_when_request_has_channel() -> None:
    assert _peer_kwarg(_ChannelRequest, "chan-obj") == {"channel": "chan-obj"}


def test_peer_kwarg_raises_when_neither_supported() -> None:
    class _Bogus:
        def __init__(self, *, foo: str) -> None: ...

    with pytest.raises(RuntimeError, match="neither `peer` nor `channel`"):
        _peer_kwarg(_Bogus, "x")


def test_extract_topic_id_finds_id_in_updates() -> None:
    class _Upd:
        def __init__(self, id_value: int) -> None:
            self.id = id_value

    class _Wrapper:
        def __init__(self) -> None:
            self.updates = [_Upd(0), _Upd(99)]

    assert _extract_topic_id(_Wrapper()) == 99


def test_extract_topic_id_falls_back_to_message_field() -> None:
    class _Upd:
        def __init__(self, message_value: int) -> None:
            self.message = message_value

    class _Wrapper:
        def __init__(self) -> None:
            self.updates = [_Upd(7)]

    assert _extract_topic_id(_Wrapper()) == 7


def test_extract_topic_id_raises_when_updates_empty() -> None:
    class _Wrapper:
        updates: list[Any] = []

    with pytest.raises(RuntimeError, match="did not return a topic id"):
        _extract_topic_id(_Wrapper())


@pytest.mark.asyncio
async def test_create_topic_uses_peer_and_random_id_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Newer Telethon: signature is ``(peer, title, random_id)``."""
    response = type("R", (), {"updates": [type("U", (), {"id": 11})()]})()
    client = _RecordingClient(response=response)
    monkeypatch.setattr(
        tb,
        "_import_forum_request",
        lambda name: _PeerRequest if name == "CreateForumTopicRequest" else None,
    )

    backend = TelethonTopicBackend(client)
    topic_id = await backend.create_topic(chat_id=42, name="hello")

    assert topic_id == 11
    assert client._peer_lookups == [42]
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert isinstance(sent, _PeerRequest)
    assert sent.title == "hello"
    assert sent.peer.chat_id == 42
    # random_id must be a positive 63-bit int
    params = inspect.signature(_PeerRequest).parameters
    assert "random_id" in params
    assert isinstance(sent.random_id, int) and sent.random_id > 0


@pytest.mark.asyncio
async def test_create_topic_falls_back_to_channel_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy Telethon: signature is ``(channel, title)``."""
    response = type("R", (), {"updates": [type("U", (), {"id": 5})()]})()
    client = _RecordingClient(response=response)
    monkeypatch.setattr(
        tb,
        "_import_forum_request",
        lambda name: _ChannelRequest if name == "CreateForumTopicRequest" else None,
    )

    backend = TelethonTopicBackend(client)
    topic_id = await backend.create_topic(chat_id=7, name="hi")

    assert topic_id == 5
    assert isinstance(client.calls[0], _ChannelRequest)
    assert client.calls[0].channel.chat_id == 7
    assert client.calls[0].title == "hi"
