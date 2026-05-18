"""Telethon-backed :class:`TopicBackend` implementation.

Kept separate from :mod:`service` so the domain layer stays free of Telethon
imports.
"""

from __future__ import annotations

import inspect
import secrets
from typing import Any

from telegram_planfix_assistant.topics.service import TopicSummary


def _import_forum_request(name: str) -> Any:
    """Locate a forum-topic request type across Telethon versions.

    Telethon shipped these requests under ``tl.functions.channels`` in older
    builds and moved them to ``tl.functions.messages`` later. We try both so
    the adapter works against whichever Telethon the host has installed.
    """
    try:
        module = __import__(
            "telethon.tl.functions.channels", fromlist=[name]
        )
        return getattr(module, name)
    except (ImportError, AttributeError):
        module = __import__(
            "telethon.tl.functions.messages", fromlist=[name]
        )
        return getattr(module, name)


def _peer_kwarg(request_cls: Any, peer: Any) -> dict[str, Any]:
    """Return the entity kwarg for ``request_cls`` (``peer`` vs ``channel``)."""
    params = inspect.signature(request_cls).parameters
    if "peer" in params:
        return {"peer": peer}
    if "channel" in params:
        return {"channel": peer}
    raise RuntimeError(
        f"{request_cls.__name__} accepts neither `peer` nor `channel`"
    )


def _extract_topic_id(updates: Any) -> int:
    """Find the new topic id inside the Telethon ``Updates`` result.

    ``CreateForumTopicRequest`` returns an ``Updates`` envelope whose
    ``updates`` list contains ``UpdateMessageID`` carrying the new topic's
    root message id — which Telegram also uses as the topic id.
    """
    for update in getattr(updates, "updates", None) or []:
        topic_id = getattr(update, "id", None)
        if topic_id is None:
            topic_id = getattr(update, "message", None)
        if isinstance(topic_id, int) and topic_id > 0:
            return topic_id
    raise RuntimeError("Telegram did not return a topic id for the new topic")


class TelethonTopicBackend:
    """Adapter from the Telethon ``TelegramClient`` to :class:`TopicBackend`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create_topic(self, *, chat_id: int, name: str) -> int:
        request_cls = _import_forum_request("CreateForumTopicRequest")
        peer = await self._client.get_input_entity(chat_id)
        kwargs: dict[str, Any] = {**_peer_kwarg(request_cls, peer), "title": name}
        params = inspect.signature(request_cls).parameters
        if "random_id" in params:
            # Telethon's newer signature requires an explicit random_id so that
            # replays don't accidentally create duplicates server-side.
            kwargs["random_id"] = secrets.randbits(63)
        result = await self._client(request_cls(**kwargs))
        return _extract_topic_id(result)

    async def send_message(
        self, *, chat_id: int, text: str, topic_id: int | None = None
    ) -> int:
        kwargs: dict[str, Any] = {}
        if topic_id is not None:
            kwargs["reply_to"] = topic_id
        sent = await self._client.send_message(chat_id, text, **kwargs)
        return int(getattr(sent, "id", 0))

    async def close_topic(self, *, chat_id: int, topic_id: int) -> None:
        request_cls = _import_forum_request("EditForumTopicRequest")
        peer = await self._client.get_input_entity(chat_id)
        kwargs: dict[str, Any] = {
            **_peer_kwarg(request_cls, peer),
            "topic_id": topic_id,
            "closed": True,
        }
        await self._client(request_cls(**kwargs))

    async def list_topics(self, *, chat_id: int) -> list[TopicSummary]:
        request_cls = _import_forum_request("GetForumTopicsRequest")
        peer = await self._client.get_input_entity(chat_id)
        kwargs: dict[str, Any] = {
            **_peer_kwarg(request_cls, peer),
            "offset_date": None,
            "offset_id": 0,
            "offset_topic": 0,
            "limit": 100,
        }
        result = await self._client(request_cls(**kwargs))
        out: list[TopicSummary] = []
        for topic in getattr(result, "topics", None) or []:
            topic_id = getattr(topic, "id", None)
            title = getattr(topic, "title", None)
            closed = bool(getattr(topic, "closed", False))
            if isinstance(topic_id, int) and isinstance(title, str):
                out.append(TopicSummary(topic_id=topic_id, title=title, closed=closed))
        return out
