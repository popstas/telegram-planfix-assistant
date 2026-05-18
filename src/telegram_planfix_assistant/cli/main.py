"""Top-level Typer application wiring all subcommand groups."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import ConfigError, load_config
from telegram_planfix_assistant.health import collect_health, default_database_path
from telegram_planfix_assistant.persistence import (
    OperationNotFoundError,
    OperationStatus,
    OperationStore,
)
from telegram_planfix_assistant.telegram_client.session import (
    TelethonSessionManager,
)

app = typer.Typer(
    name="telegram-planfix-assistant",
    help="Telegram automation service for the Planfix integration.",
    no_args_is_help=True,
    add_completion=False,
)


def _placeholder(group: str, action: str) -> None:
    typer.echo(
        f"[telegram-planfix-assistant] {group} {action}: not implemented yet "
        "(scheduled in a later task).",
        err=True,
    )
    raise typer.Exit(code=2)


# --- auth -------------------------------------------------------------------


def _format_me(me: object) -> str:
    parts: list[str] = []
    for attr in ("id", "username", "phone", "first_name", "last_name"):
        value = getattr(me, attr, None)
        if value:
            parts.append(f"{attr}={value}")
    return ", ".join(parts) if parts else repr(me)


def _build_session_manager(config_path: Path | None) -> TelethonSessionManager:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    return TelethonSessionManager(config.telegram)


@app.command("auth")
def auth_cmd(
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Interactive Telethon login for the technical account."""
    manager = _build_session_manager(config_path)

    async def _run() -> None:
        state = await manager.state()
        if state.authorized:
            typer.echo(
                "Telethon session already authorized: "
                f"{_format_me(state.me)} "
                f"(label={state.account_label}, session={state.session_path})"
            )
            await manager.disconnect()
            return

        typer.echo(
            f"Starting interactive login for label '{state.account_label}'. "
            f"Session will be stored at: {state.session_path}"
        )
        new_state = await manager.interactive_login()
        typer.echo(
            "Login successful: "
            f"{_format_me(new_state.me)} (session={new_state.session_path})"
        )
        await manager.disconnect()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=130) from None
    except Exception as exc:  # pragma: no cover - depends on Telegram
        typer.echo(f"Auth failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# --- health -----------------------------------------------------------------


def _load_config_or_exit(config_path: Path | None):
    try:
        return load_config(config_path)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@app.command("health")
def health_cmd(
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Report service health (telegram session, database, default folder)."""
    config = _load_config_or_exit(config_path)
    manager = TelethonSessionManager(config.telegram)

    async def _run() -> dict[str, str]:
        try:
            report = await collect_health(config, session_manager=manager)
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass
        return report.to_dict()

    try:
        payload = asyncio.run(_run())
    except Exception as exc:  # pragma: no cover - defensive guard
        typer.echo(f"Health check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True))


# --- version ----------------------------------------------------------------


@app.command("version")
def version_cmd() -> None:
    """Print the installed version."""
    typer.echo(__version__)


# --- groups -----------------------------------------------------------------

groups_app = typer.Typer(help="Manage Telegram supergroups.", no_args_is_help=True)
app.add_typer(groups_app, name="groups")


@groups_app.command("create")
def groups_create() -> None:
    """Create a supergroup (Task 7)."""
    _placeholder("groups", "create")


# --- topics -----------------------------------------------------------------

topics_app = typer.Typer(help="Manage forum topics.", no_args_is_help=True)
app.add_typer(topics_app, name="topics")


@topics_app.command("create")
def topics_create() -> None:
    """Create a single topic (Task 8)."""
    _placeholder("topics", "create")


@topics_app.command("bulk-create")
def topics_bulk_create() -> None:
    """Bulk-create topics (Task 9)."""
    _placeholder("topics", "bulk-create")


@topics_app.command("close")
def topics_close() -> None:
    """Close a topic (Task 10)."""
    _placeholder("topics", "close")


# --- members ----------------------------------------------------------------

members_app = typer.Typer(help="Manage group membership.", no_args_is_help=True)
app.add_typer(members_app, name="members")


@members_app.command("bulk-add")
def members_bulk_add() -> None:
    """Bulk-add members (Task 11)."""
    _placeholder("members", "bulk-add")


@members_app.command("bulk-remove")
def members_bulk_remove() -> None:
    """Bulk-remove members (Task 12)."""
    _placeholder("members", "bulk-remove")


# --- messages ---------------------------------------------------------------

messages_app = typer.Typer(help="Send messages and service commands.", no_args_is_help=True)
app.add_typer(messages_app, name="messages")


@messages_app.command("send")
def messages_send() -> None:
    """Send a message or command (Task 13)."""
    _placeholder("messages", "send")


# --- folders ----------------------------------------------------------------

folders_app = typer.Typer(help="Inspect and manage chat folders.", no_args_is_help=True)
app.add_typer(folders_app, name="folders")


def _build_folder_backend(config_path: Path | None):
    """Construct a Telethon-backed folder backend for CLI invocations.

    Importing the adapter lazily keeps `folders --help` working in
    environments where Telethon is partially unavailable, and keeps the
    placeholder commands cheap.
    """
    config = _load_config_or_exit(config_path)
    manager = TelethonSessionManager(config.telegram)
    from telegram_planfix_assistant.folders import TelethonFolderBackend

    async def _open():
        client = await manager.get_client()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is not authorized; run "
                "`telegram-planfix-assistant auth` first."
            )
        return TelethonFolderBackend(client)

    return config, manager, _open


def _resolve_folder_name(
    explicit: str | None,
    config_path: Path | None,
) -> tuple[str, int | None, Path | None]:
    """Resolve `--folder-name`, falling back to the configured default folder."""
    if explicit:
        return explicit, None, config_path
    config = _load_config_or_exit(config_path)
    default = config.telegram.default_chat_folder
    return default.folder_name, default.folder_id, config_path


@folders_app.command("inspect")
def folders_inspect(
    folder_name: str | None = typer.Option(
        None,
        "--folder-name",
        help="Folder name to inspect (defaults to telegram.default_chat_folder.folder_name).",
    ),
    folder_id: int | None = typer.Option(
        None,
        "--folder-id",
        help="Optional folder id cross-check.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Inspect a chat folder and list its chats."""
    from telegram_planfix_assistant.folders import (
        FolderError,
        inspect_folder,
    )

    resolved_name, default_fid, cfg_path = _resolve_folder_name(
        folder_name, config_path
    )
    effective_fid = folder_id if folder_id is not None else default_fid
    _config, manager, open_backend = _build_folder_backend(cfg_path)

    async def _run() -> dict[str, object]:
        try:
            backend = await open_backend()
            snapshot = await inspect_folder(
                backend, folder_name=resolved_name, folder_id=effective_fid
            )
            return snapshot.to_dict()
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass

    try:
        payload = asyncio.run(_run())
    except FolderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"folders inspect failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True))


@folders_app.command("add-chat")
def folders_add_chat(
    folder_name: str | None = typer.Option(
        None,
        "--folder-name",
        help="Folder name (defaults to telegram.default_chat_folder.folder_name).",
    ),
    chat_name: str | None = typer.Option(
        None,
        "--chat-name",
        help="Chat title (must match exactly one existing chat).",
    ),
    chat_id: int | None = typer.Option(
        None,
        "--chat-id",
        help="Numeric Telegram chat id.",
    ),
    folder_id: int | None = typer.Option(
        None,
        "--folder-id",
        help="Optional folder id cross-check.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Move an existing chat into a folder."""
    from telegram_planfix_assistant.folders import (
        FolderError,
        add_chat_to_folder,
        resolve_chat_in_folder,
    )

    if (chat_id is None) == (chat_name is None):
        typer.echo(
            "exactly one of --chat-id or --chat-name must be supplied", err=True
        )
        raise typer.Exit(code=2)

    resolved_name, default_fid, cfg_path = _resolve_folder_name(
        folder_name, config_path
    )
    effective_fid = folder_id if folder_id is not None else default_fid
    _config, manager, open_backend = _build_folder_backend(cfg_path)

    async def _run() -> dict[str, object]:
        try:
            backend = await open_backend()
            if chat_id is not None:
                chat_ref: str | int = chat_id
            else:
                # chat_name -> chat_id resolution shares the same code path as
                # the HTTP endpoint so ambiguous names fail loudly the same way.
                resolved = await resolve_chat_in_folder(
                    backend,
                    folder_name=resolved_name,
                    chat_name=chat_name or "",
                    folder_id=effective_fid,
                )
                chat_ref = resolved.chat_id
            return await add_chat_to_folder(
                backend,
                folder_name=resolved_name,
                chat_ref=chat_ref,
                folder_id=effective_fid,
            )
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass

    try:
        payload = asyncio.run(_run())
    except FolderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"folders add-chat failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True))


# --- operations -------------------------------------------------------------

operations_app = typer.Typer(help="Inspect and retry queued operations.", no_args_is_help=True)
app.add_typer(operations_app, name="operations")


def _open_store(config_path: Path | None) -> OperationStore:
    config = _load_config_or_exit(config_path)
    return OperationStore(default_database_path(config))


def _operation_summary(store: OperationStore, operation_id: str) -> dict[str, object]:
    try:
        op = store.get_operation(operation_id)
    except OperationNotFoundError as exc:
        typer.echo(f"operation {operation_id} not found", err=True)
        raise typer.Exit(code=2) from exc
    items = store.list_items(op.id)
    by_status: dict[str, int] = {}
    for it in items:
        by_status[it.status.value] = by_status.get(it.status.value, 0) + 1
    payload: dict[str, object] = op.to_dict()
    payload["items"] = {
        "total": len(items),
        "by_status": by_status,
        "needs_review": [
            {
                "id": it.id,
                "idempotency_key": it.idempotency_key,
                "error": it.error,
            }
            for it in items
            if it.status is OperationStatus.NEEDS_REVIEW
        ],
    }
    return payload


@operations_app.command("status")
def operations_status(
    operation_id: str = typer.Option(
        ...,
        "--operation-id",
        help="ID of the operation to inspect.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Show the status of an operation, including per-item summary."""
    store = _open_store(config_path)
    payload = _operation_summary(store, operation_id)
    typer.echo(json.dumps(payload, sort_keys=True, default=str))


@operations_app.command("retry")
def operations_retry(
    operation_id: str = typer.Option(
        ...,
        "--operation-id",
        help="ID of the operation to retry.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Reset a failed/needs_review operation (and its items) back to pending.

    The actual re-execution is performed by the worker queue when it next
    picks the operation up — this command only flips state so it becomes
    eligible for retry.
    """
    store = _open_store(config_path)
    try:
        op = store.get_operation(operation_id)
    except OperationNotFoundError as exc:
        typer.echo(f"operation {operation_id} not found", err=True)
        raise typer.Exit(code=2) from exc
    if op.status is OperationStatus.COMPLETED:
        typer.echo(
            f"operation {operation_id} is completed; nothing to retry", err=True
        )
        raise typer.Exit(code=2)
    reset_items = store.reset_items_for_retry(operation_id)
    if op.status is not OperationStatus.PENDING:
        store.reset_operation_for_retry(operation_id)
    typer.echo(
        json.dumps(
            {
                "operation_id": operation_id,
                "operation_reset": op.status is not OperationStatus.PENDING,
                "items_reset": [it.id for it in reset_items],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
