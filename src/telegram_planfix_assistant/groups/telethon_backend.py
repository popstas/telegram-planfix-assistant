"""Telethon-backed :class:`GroupBackend` implementation.

Kept separate from :mod:`service` so the domain layer stays free of Telethon
imports. Each method translates a domain verb (``create_supergroup``,
``add_member``, ``promote_admin``, ``create_invite_link``, ``send_message``)
into the corresponding Telethon call.
"""

from __future__ import annotations

from typing import Any

from telegram_planfix_assistant.telegram_client.errors import translate_flood_wait


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

        try:
            result = await self._client(
                CreateChannelRequest(
                    title=title,
                    about=about or "",
                    megagroup=True,
                )
            )
        except Exception as exc:
            raise translate_flood_wait(exc) from exc
        # CreateChannelRequest returns Updates; the new channel sits in `.chats`.
        chats = getattr(result, "chats", None) or []
        if not chats:
            raise RuntimeError(
                "Telegram did not return a chat for the created supergroup"
            )
        channel = chats[0]
        chat_id = int(getattr(channel, "id", 0))
        if enable_topics:
            # Telethon 1.43 added the `tabs` argument to ToggleForumRequest.
            # Older Telethon versions reject the kwarg; try the new signature
            # first and fall back for callers stuck on the legacy build.
            try:
                await self._client(
                    ToggleForumRequest(channel=channel, enabled=True, tabs=True)
                )
            except TypeError:
                try:
                    await self._client(
                        ToggleForumRequest(channel=channel, enabled=True)
                    )
                except Exception as exc:
                    raise translate_flood_wait(exc) from exc
            except Exception as exc:
                raise translate_flood_wait(exc) from exc
        return chat_id

    async def add_member(self, *, chat_id: int, user: str) -> None:
        from telethon.tl.functions.channels import InviteToChannelRequest

        try:
            channel = await self._client.get_input_entity(chat_id)
            member = await self._client.get_input_entity(user)
            await self._client(
                InviteToChannelRequest(channel=channel, users=[member])
            )
        except Exception as exc:
            raise translate_flood_wait(exc) from exc

    async def promote_admin(self, *, chat_id: int, user: str) -> None:
        from telethon.tl.functions.channels import EditAdminRequest
        from telethon.tl.types import ChatAdminRights

        try:
            channel = await self._client.get_input_entity(chat_id)
            member = await self._client.get_input_entity(user)
        except Exception as exc:
            raise translate_flood_wait(exc) from exc
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
        try:
            await self._client(
                EditAdminRequest(
                    channel=channel,
                    user_id=member,
                    admin_rights=rights,
                    rank="admin",
                )
            )
        except Exception as exc:
            raise translate_flood_wait(exc) from exc

    async def create_invite_link(self, *, chat_id: int) -> str:
        from telethon.tl.functions.messages import ExportChatInviteRequest

        try:
            channel = await self._client.get_input_entity(chat_id)
            result = await self._client(ExportChatInviteRequest(peer=channel))
        except Exception as exc:
            raise translate_flood_wait(exc) from exc
        link = getattr(result, "link", None)
        if not link:
            raise RuntimeError(
                f"Telegram did not return an invite link for chat {chat_id}"
            )
        return str(link)

    async def send_message(self, *, chat_id: int, text: str) -> int:
        try:
            sent = await self._client.send_message(chat_id, text)
        except Exception as exc:
            raise translate_flood_wait(exc) from exc
        message_id = getattr(sent, "id", 0)
        return int(message_id)
