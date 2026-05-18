# telegram-planfix-assistant MVP

## Overview

Build `telegram-planfix-assistant` — a Telegram automation service for the Planfix ↔ Telegram integration. It exposes three interfaces over one domain layer: an HTTP API (primary entry point for Planfix and automations), a CLI that mirrors every HTTP endpoint for manual and batch use, and a worker/queue that performs Telegram operations with throttling and `FLOOD_WAIT` handling.

The service runs on MTProto via Telethon under a technical Telegram user account, because the Bot API cannot create supergroups, add users before a dialog is started, or perform several other administrative actions this project needs. It must support creating client supergroups with topics enabled, placing them into a configured Telegram chat folder, generating invite links, adding `@planfix_bot` and reserve accounts, bulk membership changes, bulk topic creation, topic closing, and structured message sending — all with idempotency, queueing, and `FLOOD_WAIT` resilience.

## Context

- Adopted from `docs/init-plan.md` (Russian-language technical specification, ~875 lines).
- Out of scope: Planfix-side objects/fields/scenarios, replacing `@planfix_bot`, business-status logic for services, WhatsApp, per-topic access control (Telegram does not support it).
- Stack: Python 3.12+, Telethon (MTProto), FastAPI (or equivalent), SQLite for operation state, Docker image, config at `data/config.yml`.
- The `data/` directory must be in `.gitignore`; it holds the Telethon session, SQLite database, and secrets.
- HTTP default port: `8085`. Bearer-token auth.
- Telegram chat folder is configured in `data/config.yml` by `folder_name` (with optional `folder_id` cross-check) and must already exist — the service must never auto-create folders.
- Idempotency states: `pending`, `completed`, `failed`, `needs_review`. Operations that have an undefined outcome after timeout become `needs_review` and are not auto-retried.
- Use `.venv` for the Python virtual environment.

## Development Approach

- Testing approach: regular (write tests for every feature; integration tests against a Telegram test contour where feasible)
- Complete each task fully before moving to the next
- Share one domain layer between HTTP, CLI, and worker — never duplicate logic across surfaces
- Update this plan when scope changes during implementation

## Testing Strategy

- Unit tests required for every code-changing task
- Integration tests for Telegram interactions should run against a dedicated test contour: technical account, reserve technical account, test chat folder, `@planfix_bot`, 2-3 test users, a test group
- Run project tests after each task before proceeding
- Final task runs the full suite, the linter, and walks the MVP acceptance checklist from the source spec

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Update plan if implementation deviates from original scope

## Technical Details

### Configuration shape (`data/config.yml`)

```yaml
telegram:
  api_id: 123456
  api_hash: "telegram_api_hash"
  session_path: /data/telegram-planfix-assistant.session
  main_account_label: planfix-assistant-main
  reserve_admins:
    - "@reserve_account"
  reserve_members:
    - "@planfix_bot"
  default_chat_folder:
    folder_id: 2
    folder_name: "Planfix clients"
  defaults:
    enable_topics: true
    create_invite_link: true

http:
  host: "0.0.0.0"
  port: 8085
  bearer_token: "secret_token"

queue:
  max_parallel_telegram_ops: 1
  default_retry_delay_seconds: 30
  flood_wait_safety_margin_seconds: 5

logging:
  level: INFO
```

### Idempotency keys

- Group create: `planfix_task_id` if present, else `title`
- Topic create: `planfix_task_id` if present, else `telegram_chat_id + topic_name`
- Bulk item: `operation_id + per-item key (planfix_task_id / topic_name / user)`
- Topic close: `telegram_chat_id + telegram_topic_id`
- Member add/remove: `telegram_chat_id + user`

### HTTP endpoints

- `POST /telegram/groups` — create group
- `POST /telegram/topics` — create single topic
- `POST /telegram/topics/bulk-create` — bulk topic creation
- `POST /telegram/topics/{topic_id}/close` — close topic
- `POST /telegram/groups/{chat_id}/members/bulk-add` — bulk add members
- `POST /telegram/groups/{chat_id}/members/bulk-remove` — bulk remove members
- `POST /telegram/messages` — send message/command, supports mass mode (folder + topic name)
- `GET /telegram/folders/{folder_name}` — inspect folder
- `POST /telegram/folders/{folder_name}/chats` — move chat into folder
- `GET /health` — healthcheck

### CLI shape

Every HTTP endpoint has a CLI counterpart:

```
telegram-planfix-assistant <resource> <action> [options]
```

Plus CLI-only commands:

- `telegram-planfix-assistant auth` — interactive Telethon login
- `telegram-planfix-assistant operations status --operation-id <id>`
- `telegram-planfix-assistant operations retry --operation-id <id>`

CLI conventions:

- `--folder-name` falls back to `telegram.default_chat_folder.folder_name`
- `--chat-name` + `--folder-name` resolves to `chat_id` by exact title match within the folder; ambiguous matches must fail loudly with the list of matches
- `--topic-name` resolves to `topic_id` within the selected chat
- `--no-reserve` disables adding configured `reserve_admins`/`reserve_members`
- Destructive bulk commands support `--dry-run`, require `--yes` unless explicitly confirmed, and require `--force` to touch technical accounts or `@planfix_bot`

### Error taxonomy

The service must distinguish: user not found, user blocked by privacy settings, user already present, user not present, no admin rights, chat not found, topic not found, folder not found, Telethon session unauthorized, Telegram `FLOOD_WAIT`, undefined-outcome timeout (`needs_review`).

### Structured logging fields

`operation_id`, HTTP request ID or CLI invocation ID, `planfix_task_id`, `chat_name`, `topic_name`, `telegram_chat_id`, `telegram_topic_id`, operation type, bulk item, result, error, duration.

### Minimum alerting hooks

Telethon session unauthorized, repeated `FLOOD_WAIT` in a row, stuck bulk operation, operation moved to `needs_review`, default chat folder unavailable, error rate above threshold.

## Implementation Steps

### Task 1: Project scaffolding

- [x] create Python 3.12+ project layout under `src/telegram_planfix_assistant/`
- [x] create `.venv` and pin dependencies (Telethon, FastAPI or equivalent, uvicorn, pydantic, SQLAlchemy or sqlite-utils, click/typer for CLI, structlog)
- [x] add `pyproject.toml` (or equivalent) with `telegram-planfix-assistant` CLI entrypoint
- [x] add `.gitignore` excluding `data/`, `.venv/`, `__pycache__/`, build artifacts
- [x] add config loader for `data/config.yml` with validation and clear error messages
- [x] add FastAPI app skeleton with bearer-token auth middleware reading `http.bearer_token` from config
- [x] add CLI skeleton with subcommand groups: `auth`, `health`, `groups`, `topics`, `members`, `messages`, `folders`, `operations`
- [x] write tests for config loader (valid file, missing keys, missing file)
- [x] run project tests - must pass before next task

### Task 2: Telethon session and `auth` CLI

- [x] implement Telethon session wrapper reading `api_id`, `api_hash`, `session_path` from config
- [x] implement `telegram-planfix-assistant auth` interactive login (phone, code, optional 2FA password) writing session to configured path
- [x] make re-running `auth` show the current authorized account and not re-prompt unnecessarily
- [x] expose a single shared Telethon client used by HTTP, CLI, and worker
- [x] write tests for session state detection (unauthorized vs authorized) using mock Telethon client
- [x] run project tests - must pass before next task

### Task 3: Healthcheck (HTTP + CLI)

- [x] implement `GET /health` returning `status`, `telegram_session` (`authorized`/`unauthorized`), `database` (`ok`/`error`), `default_folder` (`ok`/`missing`)
- [x] implement `telegram-planfix-assistant health` CLI returning the same payload
- [x] add startup probe so the service responds to `/health` even when Telegram is reachable but session is unauthorized
- [x] write tests covering all health states (session authorized/unauthorized, DB ok/error, folder ok/missing)
- [x] run project tests - must pass before next task

### Task 4: Persistence and idempotency layer

- [x] design SQLite schema: `operations` (id, type, status, request_payload, result_payload, error, created_at, updated_at), `operation_items` (id, operation_id, idempotency_key, status, result, error), `idempotency_index` (key, operation_id)
- [x] implement migrations or schema bootstrap on startup
- [x] implement state machine: `pending` → `completed` | `failed` | `needs_review`
- [x] implement replay semantics: `completed` returns saved result, `pending` waits up to a configured timeout or returns 503, `failed` returns saved error, `needs_review` does not auto-retry
- [x] implement idempotency-key computation per operation type as defined in Technical Details
- [x] write tests for replay of each state, for duplicate-key collision behavior, and for the per-item idempotency in bulk operations
- [x] run project tests - must pass before next task

### Task 5: Queue and worker with `FLOOD_WAIT` handling

- [x] implement async worker queue with bounded parallelism (`queue.max_parallel_telegram_ops`)
- [x] handle `FLOOD_WAIT` as a graceful pause respecting `flood_wait_safety_margin_seconds` rather than a fatal error
- [x] persist per-item progress for bulk operations so a restart resumes from the last incomplete item
- [x] implement `telegram-planfix-assistant operations status --operation-id <id>` and `... operations retry --operation-id <id>` CLI commands
- [x] surface `needs_review` items in `operations status` output
- [x] write tests covering `FLOOD_WAIT` retry, restart resumption, `operations status`, and `operations retry` for failed and `needs_review` items
- [x] run project tests - must pass before next task

### Task 6: Chat folder resolution and operations

- [ ] implement helper that resolves `--folder-name` (and optional `folder_id` cross-check) to a Telegram folder peer list, returning a clear error if missing — never auto-create
- [ ] implement helper that resolves `--chat-name` within a folder to a `telegram_chat_id`; ambiguous matches must fail with the list of duplicates
- [ ] implement `GET /telegram/folders/{folder_name}` + `telegram-planfix-assistant folders inspect --folder-name <name>` returning `folder_id`, `folder_name`, `chats_count`, `chats`
- [ ] implement `POST /telegram/folders/{folder_name}/chats` + `telegram-planfix-assistant folders add-chat --folder-name <name> --chat-name <name>` to move an existing chat into a folder
- [ ] mark per-peer folder failures as `needs_review` rather than silent success
- [ ] write tests for folder resolution (found / missing / `folder_id` mismatch), chat-name resolution (unique / ambiguous / missing), inspect, and add-chat
- [ ] run project tests - must pass before next task

### Task 7: Group create (HTTP + CLI)

- [ ] implement `POST /telegram/groups` and `telegram-planfix-assistant groups create` sharing one domain function
- [ ] create supergroup, enable topics (default from `telegram.defaults.enable_topics`), add `@planfix_bot` if listed in `members`/`reserve_members`
- [ ] add primary admins, members, reserve admins, and reserve members; promote admins to Telegram admin role
- [ ] create invite link when `create_invite_link` is true (default from `telegram.defaults.create_invite_link`)
- [ ] place the new group into the configured chat folder
- [ ] when `planfix_task_id` is provided and `@planfix_bot` is in the group, send `/task {planfix_task_id}` as a service message
- [ ] enforce idempotency by `planfix_task_id`, falling back to exact `title` match
- [ ] support CLI `--no-reserve` to skip configured reserve accounts; allow `--reserve-admin`/`--reserve-member` to add on top of config
- [ ] write tests covering: happy path, idempotent re-call by `planfix_task_id`, idempotent re-call by `title`, `--no-reserve`, missing folder, `@planfix_bot` triggering `/task` message
- [ ] run project tests - must pass before next task

### Task 8: Single topic create (HTTP + CLI)

- [ ] implement `POST /telegram/topics` and `telegram-planfix-assistant topics create`
- [ ] support CLI `--chat-id` or `--chat-name` + `--folder-name` resolution
- [ ] support CLI `--topic-name`
- [ ] first-message logic: `/task {planfix_task_id}` if id present, else `message` if provided, else duplicate the topic name
- [ ] persist `planfix_task_id → telegram_chat_id + telegram_topic_id` mapping when present
- [ ] enforce idempotency: same `planfix_task_id` returns existing topic; without it, `telegram_chat_id + topic_name` is the key
- [ ] write tests for all three first-message branches, idempotent re-calls, `--chat-name` resolution
- [ ] run project tests - must pass before next task

### Task 9: Bulk topic create (HTTP + CLI)

- [ ] implement `POST /telegram/topics/bulk-create` and `telegram-planfix-assistant topics bulk-create`
- [ ] accept JSON body and `--file <path>` CSV with columns `planfix_task_id,topic_name,message`
- [ ] run via the worker queue with FLOOD_WAIT awareness and per-item progress
- [ ] enforce per-item idempotency (by `planfix_task_id` when present, otherwise `telegram_chat_id + topic_name`)
- [ ] reuse single-topic first-message logic per item
- [ ] respect `continue_on_error` (default true): continue the batch after a single-item failure
- [ ] return aggregated response with `created`, `existed`, `failed`, and per-item results
- [ ] write tests covering CSV parsing, JSON path, per-item idempotency, `continue_on_error` behavior, restart-resume of a partially completed batch
- [ ] run project tests - must pass before next task

### Task 10: Close topic (HTTP + CLI)

- [ ] implement `POST /telegram/topics/{topic_id}/close` and `telegram-planfix-assistant topics close`
- [ ] support CLI `--topic-id` or `--topic-name` (within the resolved chat)
- [ ] do not delete topic or history
- [ ] make re-close idempotent (re-call returns `closed`)
- [ ] support optional `reason` field passed through to logs
- [ ] write tests for happy path, idempotent re-close, `--topic-name` resolution including ambiguous matches
- [ ] run project tests - must pass before next task

### Task 11: Bulk add members (HTTP + CLI)

- [ ] implement `POST /telegram/groups/{chat_id}/members/bulk-add` and `telegram-planfix-assistant members bulk-add`
- [ ] accept JSON and `--file <path>` CSV with columns `user,role`
- [ ] accept user references as `@username`, phone/contact ID, or numeric Telegram user ID where MTProto permits
- [ ] support CLI `--chat-id` or `--chat-name` + `--folder-name`
- [ ] promote to admin when `role = admin`
- [ ] handle Telegram privacy restrictions per user without failing the batch when `continue_on_error = true`
- [ ] log the failure reason per user
- [ ] write tests covering admin promotion, privacy-restricted user, already-present user, `continue_on_error` behavior
- [ ] run project tests - must pass before next task

### Task 12: Bulk remove members (HTTP + CLI)

- [ ] implement `POST /telegram/groups/{chat_id}/members/bulk-remove` and `telegram-planfix-assistant members bulk-remove`
- [ ] accept JSON and `--file <path>` CSV with column `user`
- [ ] default `mode = ban_unban` so users are removed but not blacklisted permanently
- [ ] support `--dry-run` in CLI: report the intended action per user without performing it
- [ ] require `--force` to remove technical accounts or `@planfix_bot`
- [ ] require `--yes` to confirm destructive bulk removal in CLI
- [ ] handle the Telegram service message about removal cleanly in logs
- [ ] write tests for happy path, `--dry-run`, `--force` guard, refused-without-`--yes` CLI behavior, `continue_on_error`
- [ ] run project tests - must pass before next task

### Task 13: Send message/command (HTTP + CLI)

- [ ] implement `POST /telegram/messages` and `telegram-planfix-assistant messages send`
- [ ] support targeted send: `telegram_chat_id` (or `--chat-name` + `--folder-name`) and `telegram_topic_id` (or `--topic-name`)
- [ ] support mass send: when only `folder_name` + `topic_name` are given, send to every chat in the folder that has a matching topic; skip groups without that topic and mark them `skipped` with reason `topic_not_found`
- [ ] support sending service commands like `/task <id>` without leaking secrets in logs
- [ ] return per-item results in mass mode with `chat_name`, `topic_name`, `status`, `telegram_message_id`, and `reason` on skip
- [ ] write tests for targeted send, mass send across multiple folders, skip-on-missing-topic, service-command send
- [ ] run project tests - must pass before next task

### Task 14: Structured logging and alert hooks

- [ ] configure structured (JSON) logging carrying `operation_id`, request/invocation ID, `planfix_task_id`, `chat_name`, `topic_name`, `telegram_chat_id`, `telegram_topic_id`, operation type, bulk item, result, error, duration
- [ ] redact secrets (bearer token, api_hash, invite links treated as sensitive) from logs
- [ ] add alert hooks for: Telethon session unauthorized, repeated `FLOOD_WAIT` in a row, stuck bulk operation, operation moved to `needs_review`, default chat folder unavailable, error rate above threshold
- [ ] expose alert configuration via `data/config.yml` (channel-agnostic — emit to logs at minimum, optional webhook)
- [ ] write tests covering structured log shape, secret redaction, and each alert trigger
- [ ] run project tests - must pass before next task

### Task 15: Docker image and deployment

- [ ] write a Dockerfile based on a slim Python 3.12 image
- [ ] expose port 8085, mount `data/` as a volume, run `uvicorn` (or chosen ASGI server) as the default command
- [ ] provide a `docker compose` example with the volume and environment wiring
- [ ] document how to run the `auth` CLI inside the container (interactive shell), and how to run `health`
- [ ] verify `GET /health` returns `200` from the container
- [ ] write tests or a smoke script for container startup
- [ ] run project tests - must pass before next task

### Task 16: Verify acceptance criteria

- [ ] verify project is named `telegram-planfix-assistant` and runs from Docker, `GET /health` passes
- [ ] verify `telegram-planfix-assistant auth` performs interactive Telethon login and stores session under `data/`
- [ ] verify `telegram-planfix-assistant health` reports the same status as `GET /health`
- [ ] verify config is read from `data/config.yml`, and `data/` is in `.gitignore`
- [ ] verify HTTP default port is `8085`
- [ ] verify group creation works via HTTP and CLI
- [ ] verify new group is placed into the configured chat folder
- [ ] verify chat folder is not auto-created when missing
- [ ] verify duplicate `planfix_task_id` does not create a duplicate group
- [ ] verify duplicate `title` does not create a duplicate group
- [ ] verify `/task {planfix_task_id}` is sent when `planfix_task_id` is present and `@planfix_bot` is in the group
- [ ] verify single-topic creation works via HTTP and CLI
- [ ] verify `--chat-name` lookup within `--folder-name` works
- [ ] verify `--topic-name` lookup works
- [ ] verify bulk topic creation works via HTTP and CLI, and `topics.csv` supports `planfix_task_id,topic_name,message`
- [ ] verify bulk add/remove members works via HTTP and CLI, including `--dry-run` on remove
- [ ] verify topic close works via HTTP and CLI
- [ ] verify message/command send works via HTTP and CLI, including `folder_name + topic_name` mass mode
- [ ] verify every bulk operation returns per-item results
- [ ] verify `FLOOD_WAIT` is handled by the queue without losing the operation
- [ ] verify Telegram errors are visible in responses, logs, and operation status
- [ ] run full project test suite
- [ ] run project linter - all issues must be fixed

## Post-Completion

*Items requiring manual intervention - no checkboxes, informational only*

- Decide which set of Telegram admin rights to grant managers and reserve accounts (open question from the source spec)
- Decide where the service is hosted and who watches the alerts (open question from the source spec)
- Provision the test Telegram contour (technical account, reserve account, test folder, `@planfix_bot`, 2-3 test users, test group) before running integration tests
- Telethon session must be created interactively via `telegram-planfix-assistant auth` on the target host — it cannot be checked into the repository
- Bearer token for HTTP API must be set in `data/config.yml` on the target host, not committed
