"""Telethon-backed :class:`TopicBackend` implementation.

Kept separate from :mod:`service` so the domain layer stays free of Telethon
imports.
"""

from __future__ import annotations

from typing import Any


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
        from telethon.tl.functions.channels import CreateForumTopicRequest

        channel = await self._client.get_input_entity(chat_id)
        result = await self._client(
            CreateForumTopicRequest(channel=channel, title=name)
        )
        return _extract_topic_id(result)

    async def send_message(
        self, *, chat_id: int, text: str, topic_id: int | None = None
    ) -> int:
        kwargs: dict[str, Any] = {}
        if topic_id is not None:
            kwargs["reply_to"] = topic_id
        sent = await self._client.send_message(chat_id, text, **kwargs)
        return int(getattr(sent, "id", 0))
