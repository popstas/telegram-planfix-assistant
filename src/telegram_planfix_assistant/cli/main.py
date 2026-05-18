"""Top-level Typer application wiring all subcommand groups."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from telegram_planfix_assistant import __version__
from telegram_planfix_assistant.config import ConfigError, load_config
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


@app.command("health")
def health_cmd() -> None:
    """Show service health (Task 3)."""
    _placeholder("health", "show")


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


@folders_app.command("inspect")
def folders_inspect() -> None:
    """Inspect a chat folder (Task 6)."""
    _placeholder("folders", "inspect")


@folders_app.command("add-chat")
def folders_add_chat() -> None:
    """Move an existing chat into a folder (Task 6)."""
    _placeholder("folders", "add-chat")


# --- operations -------------------------------------------------------------

operations_app = typer.Typer(help="Inspect and retry queued operations.", no_args_is_help=True)
app.add_typer(operations_app, name="operations")


@operations_app.command("status")
def operations_status() -> None:
    """Show the status of an operation (Task 5)."""
    _placeholder("operations", "status")


@operations_app.command("retry")
def operations_retry() -> None:
    """Retry a failed or needs_review operation (Task 5)."""
    _placeholder("operations", "retry")


if __name__ == "__main__":  # pragma: no cover
    app()
