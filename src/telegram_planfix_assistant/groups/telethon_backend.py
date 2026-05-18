"""Telethon-backed :class:`GroupBackend` implementation.

Kept separate from :mod:`service` so the domain layer stays free of Telethon
imports. Each method translates a domain verb (``create_supergroup``,
``add_member``, ``promote_admin``, ``create_invite_link``, ``send_message``)
into the corresponding Telethon call.
"""

from __future__ import annotations

from typing import Any


class TelethonGroupBackend:
    """Adapter from the Telethon ``TelegramClient`` to :class:`GroupBackend`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create_supergroup(
        self,
        *,
        title: str,
        about: str | None,
        enable_topics: bool,
    ) -> int:
        from telethon.tl.functions.channels import (
            CreateChannelRequest,
            ToggleForumRequest,
        )

        result = await self._client(
            CreateChannelRequest(
                title=title,
                about=about or "",
                megagroup=True,
            )
        )
        # CreateChannelRequest returns Updates; the new channel sits in `.chats`.
        chats = getattr(result, "chats", None) or []
        if not chats:
            raise RuntimeError(
                "Telegram did not return a chat for the created supergroup"
            )
        channel = chats[0]
        chat_id = int(getattr(channel, "id", 0))
        if enable_topics:
            await self._client(
                ToggleForumRequest(channel=channel, enabled=True)
            )
        return chat_id

    async def add_member(self, *, chat_id: int, user: str) -> None:
        from telethon.tl.functions.channels import InviteToChannelRequest

        channel = await self._client.get_input_entity(chat_id)
        member = await self._client.get_input_entity(user)
        await self._client(
            InviteToChannelRequest(channel=channel, users=[member])
        )

    async def promote_admin(self, *, chat_id: int, user: str) -> None:
        from telethon.tl.functions.channels import EditAdminRequest
        from telethon.tl.types import ChatAdminRights

        channel = await self._client.get_input_entity(chat_id)
        member = await self._client.get_input_entity(user)
        rights = ChatAdminRights(
            change_info=True,
            post_messages=False,
            edit_messages=False,
            delete_messages=True,
            ban_users=True,
            invite_users=True,
            pin_messages=True,
            add_admins=False,
            anonymous=False,
            manage_call=True,
            other=True,
            manage_topics=True,
        )
        await self._client(
            EditAdminRequest(
                channel=channel,
                user_id=member,
                admin_rights=rights,
                rank="admin",
            )
        )

    async def create_invite_link(self, *, chat_id: int) -> str:
        from telethon.tl.functions.messages import ExportChatInviteRequest

        channel = await self._client.get_input_entity(chat_id)
        result = await self._client(ExportChatInviteRequest(peer=channel))
        link = getattr(result, "link", None)
        if not link:
            raise RuntimeError(
                f"Telegram did not return an invite link for chat {chat_id}"
            )
        return str(link)

    async def send_message(self, *, chat_id: int, text: str) -> int:
        sent = await self._client.send_message(chat_id, text)
        message_id = getattr(sent, "id", 0)
        return int(message_id)
