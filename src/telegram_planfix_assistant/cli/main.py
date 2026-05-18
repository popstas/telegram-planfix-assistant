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


def _build_group_backends(config_path: Path | None):
    """Open the Telethon-backed group + folder backends + store for the CLI.

    Mirrors :func:`_build_folder_backend` but for group creation: we lazily
    import the Telethon adapter so `groups create --help` works in
    environments where Telethon is partially installed, and so the placeholder
    commands stay cheap.
    """
    config = _load_config_or_exit(config_path)
    manager = TelethonSessionManager(config.telegram)
    store = OperationStore(default_database_path(config))

    async def _open():
        from telegram_planfix_assistant.folders import TelethonFolderBackend
        from telegram_planfix_assistant.groups.telethon_backend import (
            TelethonGroupBackend,
        )

        client = await manager.get_client()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is not authorized; run "
                "`telegram-planfix-assistant auth` first."
            )
        return TelethonGroupBackend(client), TelethonFolderBackend(client)

    return config, manager, store, _open


@groups_app.command("create")
def groups_create(
    title: str = typer.Option(..., "--title", help="Group title."),
    planfix_task_id: str | None = typer.Option(
        None,
        "--planfix-task-id",
        help="Planfix task id; used as the primary idempotency key when set.",
    ),
    about: str | None = typer.Option(
        None,
        "--about",
        help="Optional 'about' text for the supergroup.",
    ),
    admin: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--admin",
        help="User to add as admin (repeat for multiple).",
    ),
    member: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--member",
        help="User to add as a regular member (repeat for multiple).",
    ),
    reserve_admin: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--reserve-admin",
        help="Extra reserve-admin to add on top of the configured defaults.",
    ),
    reserve_member: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--reserve-member",
        help="Extra reserve-member to add on top of the configured defaults.",
    ),
    no_reserve: bool = typer.Option(
        False,
        "--no-reserve",
        help="Skip the configured reserve_admins / reserve_members.",
    ),
    no_invite_link: bool = typer.Option(
        False,
        "--no-invite-link",
        help="Do not create an invite link even if defaults allow it.",
    ),
    no_topics: bool = typer.Option(
        False,
        "--no-topics",
        help="Do not enable topics even if defaults allow it.",
    ),
    folder_name: str | None = typer.Option(
        None,
        "--folder-name",
        help="Target folder (defaults to telegram.default_chat_folder.folder_name).",
    ),
    folder_id: int | None = typer.Option(
        None,
        "--folder-id",
        help="Optional folder id cross-check.",
    ),
    skip_folder: bool = typer.Option(
        False,
        "--skip-folder",
        help="Do not place the new group into any folder.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Create a Telegram supergroup for a Planfix client."""
    from telegram_planfix_assistant.groups import (
        GroupCreateFailed,
        GroupCreateNeedsReview,
        GroupCreatePending,
        GroupCreateRequest,
        create_group,
    )

    config, manager, store, open_backends = _build_group_backends(config_path)

    # Merge configured reserves with extra ones from CLI: extras add on top.
    extra_admins = list(reserve_admin or [])
    extra_members = list(reserve_member or [])
    if no_reserve:
        reserve_admins_arg: list[str] | None = list(extra_admins)
        reserve_members_arg: list[str] | None = list(extra_members)
    elif extra_admins or extra_members:
        reserve_admins_arg = list(config.telegram.reserve_admins) + extra_admins
        reserve_members_arg = list(config.telegram.reserve_members) + extra_members
    else:
        reserve_admins_arg = None
        reserve_members_arg = None

    request = GroupCreateRequest(
        title=title,
        planfix_task_id=planfix_task_id,
        about=about,
        admins=list(admin or []),
        members=list(member or []),
        reserve_admins=reserve_admins_arg,
        reserve_members=reserve_members_arg,
        skip_reserve=no_reserve and not (extra_admins or extra_members),
        enable_topics=False if no_topics else None,
        create_invite_link=False if no_invite_link else None,
        folder_name=folder_name,
        folder_id=folder_id,
        skip_folder=skip_folder,
    )

    async def _run() -> dict[str, object]:
        try:
            group_backend, folder_backend = await open_backends()
            result, op = await create_group(
                backend=group_backend,
                folder_backend=folder_backend,
                store=store,
                config=config.telegram,
                request=request,
            )
            payload = result.to_dict()
            payload["operation_id"] = op.id
            payload["operation_status"] = op.status.value
            return payload
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass

    try:
        payload = asyncio.run(_run())
    except (GroupCreatePending, GroupCreateNeedsReview, GroupCreateFailed) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"groups create failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True, default=str))


# --- topics -----------------------------------------------------------------

topics_app = typer.Typer(help="Manage forum topics.", no_args_is_help=True)
app.add_typer(topics_app, name="topics")


def _build_topic_backends(config_path: Path | None):
    """Open the Telethon-backed topic + folder backends + store for the CLI.

    Mirrors :func:`_build_group_backends`: lazy Telethon imports keep
    ``topics create --help`` cheap.
    """
    config = _load_config_or_exit(config_path)
    manager = TelethonSessionManager(config.telegram)
    store = OperationStore(default_database_path(config))

    async def _open():
        from telegram_planfix_assistant.folders import TelethonFolderBackend
        from telegram_planfix_assistant.topics.telethon_backend import (
            TelethonTopicBackend,
        )

        client = await manager.get_client()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is not authorized; run "
                "`telegram-planfix-assistant auth` first."
            )
        return TelethonTopicBackend(client), TelethonFolderBackend(client)

    return config, manager, store, _open


@topics_app.command("create")
def topics_create(
    topic_name: str = typer.Option(..., "--topic-name", help="Topic title."),
    chat_id: int | None = typer.Option(
        None,
        "--chat-id",
        help="Numeric Telegram chat id of the supergroup.",
    ),
    chat_name: str | None = typer.Option(
        None,
        "--chat-name",
        help="Chat title (resolved within --folder-name).",
    ),
    folder_name: str | None = typer.Option(
        None,
        "--folder-name",
        help="Folder used for --chat-name lookup "
        "(defaults to telegram.default_chat_folder.folder_name).",
    ),
    folder_id: int | None = typer.Option(
        None,
        "--folder-id",
        help="Optional folder id cross-check.",
    ),
    planfix_task_id: str | None = typer.Option(
        None,
        "--planfix-task-id",
        help="Planfix task id; primary idempotency key and `/task <id>` trigger.",
    ),
    message: str | None = typer.Option(
        None,
        "--message",
        help="Optional first message text (overrides topic-name duplication).",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Create a single forum topic in an existing supergroup."""
    from telegram_planfix_assistant.folders import (
        FolderError,
        resolve_chat_in_folder,
    )
    from telegram_planfix_assistant.topics import (
        TopicCreateFailed,
        TopicCreateNeedsReview,
        TopicCreatePending,
        TopicCreateRequest,
        create_topic,
    )

    if (chat_id is None) == (chat_name is None):
        typer.echo(
            "exactly one of --chat-id or --chat-name must be supplied", err=True
        )
        raise typer.Exit(code=2)

    config, manager, store, open_backends = _build_topic_backends(config_path)

    if chat_name is not None:
        resolved_folder_name, default_fid, _ = _resolve_folder_name(
            folder_name, config_path
        )
        effective_folder_id = folder_id if folder_id is not None else default_fid
    else:
        resolved_folder_name = folder_name
        effective_folder_id = folder_id

    async def _run() -> dict[str, object]:
        try:
            topic_backend, folder_backend = await open_backends()
            if chat_id is not None:
                resolved_chat_id = chat_id
            else:
                resolved = await resolve_chat_in_folder(
                    folder_backend,
                    folder_name=resolved_folder_name or "",
                    chat_name=chat_name or "",
                    folder_id=effective_folder_id,
                )
                resolved_chat_id = resolved.chat_id

            request = TopicCreateRequest(
                telegram_chat_id=resolved_chat_id,
                topic_name=topic_name,
                planfix_task_id=planfix_task_id,
                message=message,
            )
            result, op = await create_topic(
                backend=topic_backend, store=store, request=request
            )
            payload = result.to_dict()
            payload["operation_id"] = op.id
            payload["operation_status"] = op.status.value
            return payload
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass

    try:
        payload = asyncio.run(_run())
    except (TopicCreatePending, TopicCreateNeedsReview, TopicCreateFailed) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except FolderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"topics create failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True, default=str))


def _parse_bulk_topics_csv(path: Path) -> list[dict[str, object]]:
    """Read a `planfix_task_id,topic_name,message` CSV into bulk items.

    ``planfix_task_id`` and ``message`` may be empty per row; ``topic_name``
    is required. Lines starting with ``#`` and blank rows are ignored so
    operators can keep notes alongside the data.
    """
    import csv

    out: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        required = {"topic_name"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise typer.BadParameter(
                f"CSV must have at least column 'topic_name'; got {reader.fieldnames!r}"
            )
        for row in reader:
            name = (row.get("topic_name") or "").strip()
            if not name:
                continue
            task = (row.get("planfix_task_id") or "").strip() or None
            message = row.get("message")
            if message is not None:
                message = message.strip() or None
            out.append(
                {
                    "topic_name": name,
                    "planfix_task_id": task,
                    "message": message,
                }
            )
    if not out:
        raise typer.BadParameter("CSV produced zero items")
    return out


def _parse_bulk_topics_json(path: Path) -> list[dict[str, object]]:
    """Read a JSON list of `{topic_name, planfix_task_id?, message?}`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise typer.BadParameter("JSON file must contain a top-level list")
    out: list[dict[str, object]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise typer.BadParameter("each JSON entry must be an object")
        name = str(entry.get("topic_name") or "").strip()
        if not name:
            raise typer.BadParameter("each entry needs a non-empty topic_name")
        out.append(
            {
                "topic_name": name,
                "planfix_task_id": entry.get("planfix_task_id"),
                "message": entry.get("message"),
            }
        )
    if not out:
        raise typer.BadParameter("JSON file produced zero items")
    return out


@topics_app.command("bulk-create")
def topics_bulk_create(
    chat_id: int | None = typer.Option(
        None,
        "--chat-id",
        help="Numeric Telegram chat id of the supergroup.",
    ),
    chat_name: str | None = typer.Option(
        None,
        "--chat-name",
        help="Chat title (resolved within --folder-name).",
    ),
    folder_name: str | None = typer.Option(
        None,
        "--folder-name",
        help="Folder used for --chat-name lookup "
        "(defaults to telegram.default_chat_folder.folder_name).",
    ),
    folder_id: int | None = typer.Option(
        None,
        "--folder-id",
        help="Optional folder id cross-check.",
    ),
    file: Path | None = typer.Option(  # noqa: B008
        None,
        "--file",
        help="CSV (planfix_task_id,topic_name,message) or JSON list of items.",
        exists=False,
    ),
    operation_id: str | None = typer.Option(
        None,
        "--operation-id",
        help="Idempotency id for the bulk; rerunning with the same value resumes the batch.",
    ),
    continue_on_error: bool = typer.Option(
        True,
        "--continue-on-error/--stop-on-error",
        help="Continue the batch after a single-item failure (default true).",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        "-c",
        help="Path to config.yml (defaults to data/config.yml).",
        exists=False,
    ),
) -> None:
    """Bulk-create topics from a CSV or JSON file."""
    from telegram_planfix_assistant.folders import (
        FolderError,
        resolve_chat_in_folder,
    )
    from telegram_planfix_assistant.topics import (
        BulkTopicCreateFailed,
        BulkTopicCreateNeedsReview,
        BulkTopicCreatePending,
        BulkTopicCreateRequest,
        BulkTopicItem,
        bulk_create_topics,
    )
    from telegram_planfix_assistant.worker.queue import WorkerQueue

    if (chat_id is None) == (chat_name is None):
        typer.echo(
            "exactly one of --chat-id or --chat-name must be supplied", err=True
        )
        raise typer.Exit(code=2)
    if file is None:
        typer.echo("--file is required (CSV or JSON)", err=True)
        raise typer.Exit(code=2)
    if not file.exists():
        typer.echo(f"--file path does not exist: {file}", err=True)
        raise typer.Exit(code=2)

    raw_items = (
        _parse_bulk_topics_json(file)
        if file.suffix.lower() == ".json"
        else _parse_bulk_topics_csv(file)
    )

    config, manager, store, open_backends = _build_topic_backends(config_path)

    if chat_name is not None:
        resolved_folder_name, default_fid, _ = _resolve_folder_name(
            folder_name, config_path
        )
        effective_folder_id = folder_id if folder_id is not None else default_fid
    else:
        resolved_folder_name = folder_name
        effective_folder_id = folder_id

    queue = WorkerQueue(
        store,
        max_parallel=config.queue.max_parallel_telegram_ops,
        flood_wait_safety_margin_seconds=config.queue.flood_wait_safety_margin_seconds,
        default_retry_delay_seconds=config.queue.default_retry_delay_seconds,
    )

    async def _run() -> dict[str, object]:
        try:
            topic_backend, folder_backend = await open_backends()
            if chat_id is not None:
                resolved_chat_id = chat_id
            else:
                resolved = await resolve_chat_in_folder(
                    folder_backend,
                    folder_name=resolved_folder_name or "",
                    chat_name=chat_name or "",
                    folder_id=effective_folder_id,
                )
                resolved_chat_id = resolved.chat_id

            req = BulkTopicCreateRequest(
                telegram_chat_id=resolved_chat_id,
                items=tuple(
                    BulkTopicItem(
                        topic_name=str(it["topic_name"]),
                        planfix_task_id=it.get("planfix_task_id"),
                        message=it.get("message"),
                    )
                    for it in raw_items
                ),
                continue_on_error=continue_on_error,
                operation_id=operation_id,
            )
            result, op = await bulk_create_topics(
                backend=topic_backend,
                store=store,
                queue=queue,
                request=req,
            )
            payload = result.to_dict()
            payload["operation_status"] = op.status.value
            return payload
        finally:
            try:
                await manager.disconnect()
            except Exception:
                pass

    try:
        payload = asyncio.run(_run())
    except (
        BulkTopicCreatePending,
        BulkTopicCreateNeedsReview,
        BulkTopicCreateFailed,
    ) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except FolderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"topics bulk-create failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(payload, sort_keys=True, default=str))


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
