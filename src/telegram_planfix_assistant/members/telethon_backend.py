"""Telethon-backed :class:`MemberAddBackend` implementation.

Kept separate from :mod:`service` so the domain layer stays Telethon-free.
The adapter translates the two domain verbs (``add_member``,
``promote_admin``) into the corresponding Telethon RPCs and maps known
Telegram error strings into our :class:`MemberPrivacyError` /
:class:`MemberAlreadyPresentError` so the bulk loop can categorise the
outcome.
"""

from __future__ import annotations

from typing import Any

from telegram_planfix_assistant.members.service import (
    MemberAlreadyPresentError,
    MemberPrivacyError,
)

# Telegram's RPC error class names that map to our domain errors. We match by
# class name so we don't have to import telethon at module load — the import
# happens lazily inside the methods.
_PRIVACY_RPC_ERRORS = {
    "UserPrivacyRestrictedError",
    "UserNotMutualContactError",
    "UserChannelsTooMuchError",
    "PeerFloodError",
}

_ALREADY_PRESENT_RPC_ERRORS = {
    "UserAlreadyParticipantError",
}


def _classify_rpc_error(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name in _PRIVACY_RPC_ERRORS:
        return MemberPrivacyError(str(exc) or name)
    if name in _ALREADY_PRESENT_RPC_ERRORS:
        return MemberAlreadyPresentError(str(exc) or name)
    return exc


class TelethonMemberBackend:
    """Adapter from the Telethon ``TelegramClient`` to ``MemberAddBackend``."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def add_member(self, *, chat_id: int, user: str) -> None:
        from telethon.tl.functions.channels import InviteToChannelRequest

        channel = await self._client.get_input_entity(chat_id)
        member = await self._client.get_input_entity(user)
        try:
            await self._client(
                InviteToChannelRequest(channel=channel, users=[member])
            )
        except Exception as exc:
            raise _classify_rpc_error(exc) from exc

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
