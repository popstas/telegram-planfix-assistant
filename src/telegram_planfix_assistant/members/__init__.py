"""Member-management domain shared by HTTP, CLI, and the worker."""

from telegram_planfix_assistant.members.service import (
    BulkMemberAddFailed,
    BulkMemberAddNeedsReview,
    BulkMemberAddPending,
    BulkMemberAddRequest,
    BulkMemberAddResult,
    BulkMemberItem,
    BulkMemberItemResult,
    MemberAddBackend,
    MemberAddError,
    MemberAlreadyPresentError,
    MemberPrivacyError,
    NormalizedMember,
    bulk_add_members,
    normalize_user_ref,
)

__all__ = [
    "BulkMemberAddFailed",
    "BulkMemberAddNeedsReview",
    "BulkMemberAddPending",
    "BulkMemberAddRequest",
    "BulkMemberAddResult",
    "BulkMemberItem",
    "BulkMemberItemResult",
    "MemberAddBackend",
    "MemberAddError",
    "MemberAlreadyPresentError",
    "MemberPrivacyError",
    "NormalizedMember",
    "bulk_add_members",
    "normalize_user_ref",
]
