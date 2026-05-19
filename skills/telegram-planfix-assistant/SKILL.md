---
name: telegram-planfix-assistant
description: Translate human Telegram requests into safe `telegram-planfix-assistant` CLI calls. Use when the user asks to create or close Telegram groups/topics, add or remove members, send messages, inspect or move chats between Telegram folders, or check/retry queued operations through the `telegram-planfix-assistant` project. Triggers on phrases like «добавь @username в чат», «создай топик», «закрой топик», «отправь сообщение в чат», «перенеси чат в folder», «проверь операцию», «health».
---

# telegram-planfix-assistant skill

This skill teaches the agent how to turn a human request into a safe invocation
of the existing `telegram-planfix-assistant` CLI. The agent does not build a
new Telegram bot, does not call Telethon directly, and does not change Telegram
state without an explicit human confirmation.

The detailed resource/action catalogue and the per-scenario instructions live
in later sections of this file (see "Resources & actions" and "Scenarios").
This section is the contract that applies to every action.

## Project layout the agent must know

- The CLI entry point is `telegram-planfix-assistant`. Every action below is a
  subcommand of it (`groups create`, `topics bulk-create`, `members bulk-add`,
  `messages send`, `folders inspect`, `operations status`, ...).
- The runtime config lives at `data/config.yml`. The agent reads it for
  defaults but never edits it. Notable keys:
  - `telegram.default_chat_folder.folder_name` — used as `--folder-name` when
    the human request does not name a folder explicitly.
- Telethon session, SQLite database and bearer token also live under `data/`.
  The agent does not touch them; it only invokes the CLI.
- The skill itself is loaded from `./skills/telegram-planfix-assistant/SKILL.md`
  inside the project repository.

## Primary interface: the CLI, not Telethon

- The agent's only way to change Telegram state is the CLI shipped with the
  project. Do not import Telethon, do not call the HTTP API directly, and do
  not write custom Python that bypasses the CLI.
- If a request cannot be expressed as one of the listed CLI commands, the
  agent stops and asks the human instead of inventing a new code path.

## Liveness check: `health`

Before any state-changing command in a fresh agent session run:

```bash
telegram-planfix-assistant health
```

- If `health` reports a problem (auth missing, DB unreachable, default folder
  missing, etc.), stop and report it. Do not attempt to "fix" it by running
  other commands.
- `health` is read-only; no confirmation is needed.
- Within the same session, `health` does not need to be repeated before every
  command, but it must have succeeded at least once before any change.

## Confirmation policy

Commands fall into three buckets:

1. **Read-only** — `health`, `folders inspect`, `operations status`,
   `groups get-layout`. Run them immediately, no confirmation, no
   `--dry-run`.
2. **State-changing, single object** — `groups create`, `groups set-layout`,
   `topics create`, `topics close`, `messages send` (single chat),
   `folders add-chat`, `operations retry`. Always: prepare command → run
   with `--dry-run` →
   show the plan and dry-run output → wait for explicit human confirmation
   → run the same command without `--dry-run`.
3. **State-changing, bulk or destructive** — `topics bulk-create`,
   `members bulk-add`, `members bulk-remove`, `messages send` in fan-out mode
   (folder + topic name). Same flow as bucket 2, plus the plan must show how
   many objects are affected, and `members bulk-remove` must explicitly list
   every user it would touch.

Confirmation rules:

- A confirmation is "explicit" when the human writes «да», «выполни»,
  «подтверждаю», «ок», presses a confirmation button, or similar. Silence,
  «давай посмотрим», «может быть» are not confirmations.
- A confirmation applies only to the exact command shown in the plan. If
  parameters change (different chat, different list of users, different
  text), the agent prepares a new plan and a new dry-run.
- The agent never confirms on behalf of the human and never auto-retries a
  destructive command after a failure.

## Protected accounts

- Technical/service accounts of this project and `@planfix_bot` must never be
  removed from chats automatically. `members bulk-remove` against them is
  only allowed when the human asks for it explicitly and approves the
  `--force` flag in the plan.
- If a dry-run shows that a destructive command would touch a protected
  account, the agent stops, names the account, and asks the human whether to
  proceed with `--force`.

## Temporary files in `/tmp`

Bulk commands (`topics bulk-create`, `members bulk-add`,
`members bulk-remove`, fan-out `messages send`) take a `--file` argument
pointing to a CSV or JSON file. The agent prepares those files itself.

Rules:

- Write the temporary file under `/tmp`, never inside the project repository
  and never inside `data/`.
- Use a descriptive, deterministic name so the same session can find it
  again, e.g.
  - `/tmp/telegram-planfix-assistant-topics.csv`
  - `/tmp/telegram-planfix-assistant-users.csv`
  - `/tmp/telegram-planfix-assistant-message.json`
- Show the file path and the file contents to the human as part of the plan,
  so the dry-run output can be verified against what the agent actually
  prepared.
- Treat each temporary file as belonging to the current request. The agent
  may overwrite it for a fresh request in the same session, but never edits
  a file mid-flight (between dry-run and the real run).
- Never commit these files. They live only on the runtime machine.

## The 11-step agent algorithm

Every state-changing request is processed in the same order. The agent does
not skip steps, even if the request looks obvious.

1. Read the human request (or forwarded message) verbatim.
2. Determine resource and action from the catalogue
   (see "Resources & actions"). If the request maps to no entry, stop and
   ask.
3. Extract parameters: chat, topic, users, role, text, `planfix_task_id`,
   folder. Treat anything missing as missing — never invent values.
4. If a required parameter is missing or ambiguous, ask a short clarifying
   question (one question, no preamble).
5. Run `telegram-planfix-assistant health` if it has not yet succeeded in
   the current session.
6. For bulk-style commands, prepare a temporary CSV/JSON in `/tmp` as
   described above.
7. For state-changing commands that support `--dry-run`, run with
   `--dry-run` first. The supported set is: `groups create`,
   `groups set-layout`, `topics create`, `topics bulk-create`,
   `topics close`, `members bulk-add`, `members bulk-remove`,
   `messages send`, `folders add-chat`, `operations retry`.
8. Present a short plan to the human: what was found (chat id, folder,
   matched users), the full command that would run, and the relevant parts
   of the dry-run output (`status = dry_run`, planned actions, validation
   errors if any).
9. Wait for an explicit confirmation. Do not move on after silence or vague
   replies.
10. Run the real command — the same command as in step 7, with `--dry-run`
    removed.
11. Return a short result: done / already done / skipped / Telegram error /
    needs manual review (`needs_review`). Reuse the wording from the CLI
    output; do not paraphrase error codes.

## Scope of the skill

This skill only describes how to drive the existing CLI. It does not
authorise the agent to:

- write a new Telegram bot or HTTP service;
- add Planfix-side automation;
- call Telethon or the HTTP API directly;
- change Telegram state without a confirmed plan;
- guess at chats, topics or usernames when the match is not exact;
- use real client names, real usernames or real invite links in examples.

When in doubt the agent stops and asks; that is the default, not the
exception.

## Resources & actions

The agent translates every request into exactly one resource/action pair
from the table below. If a request maps to nothing in this table, the
agent stops and asks for clarification — it does not invent a new path.

| Resource | Action | When to pick | CLI command |
|---|---|---|---|
| `auth` | `login` | The human asks to (re-)log in the technical Telegram account. The agent never runs this itself. | `telegram-planfix-assistant auth` |
| `health` | `check` | Pre-flight before any change; or the human asks "is everything alive?". | `telegram-planfix-assistant health` |
| `groups` | `create` | Create a new client supergroup (title or `planfix_task_id`), optionally with members/admins and folder placement. | `telegram-planfix-assistant groups create ...` |
| `groups` | `set-layout` | Change the topics layout (list ↔ tabs) on an existing forum supergroup. | `telegram-planfix-assistant groups set-layout ...` |
| `groups` | `get-layout` | Read the current topics layout (`list` or `tabs`) for a forum supergroup. | `telegram-planfix-assistant groups get-layout ...` |
| `topics` | `create` | Add one forum topic to an existing supergroup. | `telegram-planfix-assistant topics create ...` |
| `topics` | `bulk-create` | Add several topics to one chat from a CSV/JSON list. | `telegram-planfix-assistant topics bulk-create ...` |
| `topics` | `close` | Close (but not delete) an existing topic. | `telegram-planfix-assistant topics close ...` |
| `members` | `bulk-add` | Add one or many users to a chat, optionally as admin. | `telegram-planfix-assistant members bulk-add ...` |
| `members` | `bulk-remove` | Remove one or many users from a chat (kick or permanent ban). | `telegram-planfix-assistant members bulk-remove ...` |
| `messages` | `send` | Send a message or service command to one chat/topic, or fan it out across a folder. | `telegram-planfix-assistant messages send ...` |
| `folders` | `inspect` | Read-only: list chats inside a Telegram folder. | `telegram-planfix-assistant folders inspect ...` |
| `folders` | `add-chat` | Move an existing chat into a folder. | `telegram-planfix-assistant folders add-chat ...` |
| `operations` | `status` | Read-only: show queue status for a previously created operation. | `telegram-planfix-assistant operations status ...` |
| `operations` | `retry` | Reset a failed or `needs_review` operation so the worker can re-run it. | `telegram-planfix-assistant operations retry ...` |

### Per-pair extraction and flag rules

For every pair below: **Extract** = what the agent must lift verbatim
from the request; **Required flags** = flags without which the agent
asks instead of guessing; **From config** = flags the agent may default
from `data/config.yml`; **Temp file** = whether `/tmp/...` is needed;
**Automation** = what the agent does without asking; **Confirmation** =
when a real (non-dry-run) call is allowed; **Typical errors** = error
messages the agent must surface verbatim instead of paraphrasing.

#### `auth` / `login`

- Extract: nothing.
- Required flags: none.
- From config: none.
- Temp file: no.
- Automation: none — this is interactive and prompts for phone, code,
  and optional 2FA password in a terminal. The agent never invokes it
  and never collects credentials in chat.
- Confirmation: the human runs it themselves.
- Typical errors: `Auth failed: ...` (surface as-is and stop).

#### `health` / `check`

- Extract: nothing.
- Required flags: none.
- From config: none.
- Temp file: no.
- Automation: run automatically once per session before the first
  state-changing command (algorithm step 5).
- Confirmation: not required (read-only).
- Typical errors: any non-zero exit or any non-`ok` field in the
  output — stop, repeat the message, do not try to "fix" it.

#### `groups` / `create`

- Extract: `--title` (or `--planfix-task-id`), `--admin` and `--member`
  lists, optional `--about`.
- Required flags: at least one of `--title` or `--planfix-task-id`.
- From config: `--folder-name` defaults to
  `telegram.default_chat_folder.folder_name`; reserve admins/members
  come from `telegram.reserve_admins` / `telegram.reserve_members`.
- Temp file: no — admins and members go on the command line as repeated
  `--admin @employee_username` / `--member @member_username` flags.
- Automation: include `--planfix-task-id` when the human gives one; the
  dry-run plan must show whether `@planfix_bot` is among planned
  members so the `/task <id>` service message will actually fire.
- Confirmation: required after dry-run.
- Typical errors: `group create requires planfix_task_id or non-empty
  title`, folder errors from `resolve_folder`, `GroupCreateFailed`,
  `GroupCreateNeedsReview`.

#### `groups` / `set-layout`

- Extract: `--chat-id` (numeric supergroup id) and `--layout` when the
  human names one (`list` or `tabs`).
- Required flags: `--chat-id`.
- From config: `--layout` defaults to `telegram.defaults.topics_layout`
  when the human does not name a target layout. The agent surfaces the
  effective layout in the plan so the human can override it.
- Temp file: no.
- Automation: trigger on requests like «переключи топики чата X на
  tabs/list» or «сделай в чате X вкладки/список». Always treat this as
  a single-object state change. The operation is idempotent — replaying
  the same layout completes immediately.
- Confirmation: required after dry-run.
- Typical errors: `invalid --layout 'grid': expected 'list' or 'tabs'`
  (CLI exit code 2), `GroupLayoutSetNeedsReview` (FLOOD_WAIT — retry via
  `operations retry`), `GroupLayoutSetFailed` (Telethon error captured
  on the operation row, e.g. chat is not a forum, missing admin rights).

#### `groups` / `get-layout`

- Extract: `--chat-id`.
- Required flags: `--chat-id`.
- From config: none.
- Temp file: no.
- Automation: read-only, run immediately when the human asks «какой
  layout у чата X» / «как сейчас отображаются топики в X». No `--dry-run`.
- Confirmation: not required (read-only).
- Typical errors: `groups get-layout failed: ...` (chat not found, chat
  is not a forum, session not authorized) — surface verbatim and stop.

#### `topics` / `create`

- Extract: `--topic-name`, chat reference (`--chat-name` or `--chat-id`),
  optional `--planfix-task-id`, optional `--message`.
- Required flags: `--topic-name` and exactly one of `--chat-name` /
  `--chat-id`.
- From config: `--folder-name` defaults to the configured chat folder
  when `--chat-name` is used.
- Temp file: no.
- Automation: prefer `--chat-name` + `--folder-name` over numeric chat
  ids when the human names a chat by title.
- Confirmation: required after dry-run.
- Typical errors: `exactly one of --chat-id or --chat-name must be
  supplied`, `topic create requires non-empty topic_name`,
  `AmbiguousTopicNameError`, folder lookup errors.

#### `topics` / `bulk-create`

- Extract: chat reference, list of topics (each: `topic_name`, optional
  `planfix_task_id`, optional `message`).
- Required flags: chat reference and `--file`.
- From config: `--folder-name` default.
- Temp file: yes — write a CSV
  (`planfix_task_id,topic_name,message`) or a JSON list to
  `/tmp/telegram-planfix-assistant-topics.csv` (or `.json`).
- Automation: dedupe rows locally before writing the file; still run
  `--dry-run` and rely on its `duplicate_topic_name_in_file` /
  `duplicate_planfix_task_id_in_file` flags.
- Confirmation: required after dry-run. Show the row count and any
  warnings the dry-run reported.
- Typical errors: `--file is required (CSV or JSON)`, `--file path does
  not exist: ...`, per-row duplicate / already-exists warnings.

#### `topics` / `close`

- Extract: chat reference and topic reference (`--topic-name` or
  `--topic-id`), optional `--reason`.
- Required flags: exactly one of each pair.
- From config: `--folder-name` default.
- Temp file: no.
- Automation: none — closing is destructive even though history is
  preserved.
- Confirmation: required after dry-run; the plan must call out
  `already_closed: true` if the dry-run reports it.
- Typical errors: `TopicNotFoundError`, `AmbiguousTopicNameError`,
  folder errors.

#### `members` / `bulk-add`

- Extract: chat reference, users (with role), optional `--operation-id`.
- Required flags: chat reference, and at least one of `--file`,
  `--user`, `--admin`.
- From config: `--folder-name` default.
- Temp file: yes when there are several users — write CSV (`user,role`)
  or JSON to `/tmp/telegram-planfix-assistant-users.csv`. Inline
  `--user`/`--admin` flags are fine for one or two entries.
- Automation: build the file from the request; pass `--admin` for
  managers/leads, `--user` for everyone else.
- Confirmation: required after dry-run; the plan must list users that
  are already in the chat and users the dry-run says cannot be added.
- Typical errors: `no users supplied: use --file, --user, or --admin`,
  role validation errors, `BulkMemberAddNeedsReview` for users that
  need manual handling.

#### `members` / `bulk-remove`

- Extract: chat reference and list of users; `--mode` (`ban_unban` for
  kick, `ban` for permanent blacklist) when the human is specific.
- Required flags: chat reference and at least one of `--file`/`--user`;
  the real run also needs `--yes`.
- From config: `--folder-name` default; the protected-user set is read
  from `telegram.reserve_admins`, `telegram.reserve_members`, and the
  hard-coded `@planfix_bot`.
- Temp file: yes for multi-user removals — `/tmp/telegram-planfix-
  assistant-users.csv` (one user per line is enough).
- Automation: always run `--dry-run` first, even for a single user.
  Never include a protected user without an explicit human ask, and
  never add `--force` on the agent's own initiative.
- Confirmation: required after dry-run, must list every user the
  dry-run would touch; if any are protected, the plan must name them
  and the agent asks again before adding `--force`.
- Typical errors: `refusing to remove without --yes (or use --dry-run
  to preview)`, protected-account refusals, `BulkMemberRemoveNeedsReview`.

#### `messages` / `send`

- Extract: `--text`, chat/topic references, optional `--operation-id`.
- Required flags: `--text` plus exactly one targeting shape — targeted
  (`--chat-id`/`--chat-name` + optional `--topic-id`/`--topic-name`)
  or mass (`--mass` or no chat ref, plus `--topic-name` and
  `--folder-name`).
- From config: `--folder-name` default for both targeted resolution and
  mass mode.
- Temp file: no — message text goes via `--text`. If the human pastes a
  long multi-line message, escape it for the shell; do not write it to
  a file the CLI cannot read.
- Automation: pass service commands (`/task 123456`) verbatim.
- Confirmation: required after dry-run. Mass mode plans must list every
  resolved chat row and call out `would_skip` rows with their reason
  (`topic_not_found`, `topic_ambiguous`, `list_topics_failed: ...`).
- Typical errors: `messages send requires non-empty --text`,
  `--mass cannot be combined with --chat-id or --chat-name`,
  `mass mode requires --topic-name (and --folder-name resolves the
  folder)`, `MessageSendNeedsReview`.

#### `folders` / `inspect`

- Extract: optional `--folder-name`.
- Required flags: none — defaults to the configured chat folder.
- From config: `--folder-name` default.
- Temp file: no.
- Automation: run immediately when the human asks "what chats are in
  folder X" or to disambiguate a chat lookup.
- Confirmation: not required (read-only).
- Typical errors: `FolderError` messages (folder missing, etc.).

#### `folders` / `add-chat`

- Extract: chat reference (`--chat-name` / `--chat-id`), optional
  `--folder-name`.
- Required flags: exactly one of `--chat-name` / `--chat-id`.
- From config: `--folder-name` default.
- Temp file: no.
- Automation: none beyond defaulting the folder name.
- Confirmation: required after dry-run; if the dry-run reports
  `already_in_folder: true`, restate that to the human and skip the
  real run unless they insist.
- Typical errors: `exactly one of --chat-id or --chat-name must be
  supplied`, `FolderError`.

#### `operations` / `status`

- Extract: `--operation-id`.
- Required flags: `--operation-id`.
- From config: none.
- Temp file: no.
- Automation: run as soon as the human gives an operation id.
- Confirmation: not required (read-only).
- Typical errors: `operation <id> not found`.

#### `operations` / `retry`

- Extract: `--operation-id`.
- Required flags: `--operation-id`.
- From config: none.
- Temp file: no.
- Automation: always run `operations status` first and show the human
  the failing items before any retry attempt.
- Confirmation: required after dry-run.
- Typical errors: `operation <id> not found`, `operation <id> is
  completed; nothing to retry`.

## Scenarios

Every scenario below uses anonymized identifiers only. The agent
re-uses these patterns and replaces them with the values the human
gave — never with real usernames, chat titles, or invite links from
its own memory.

### `groups create`

Request: «Создай группу для клиента Клиент / проект, задача 123456,
менеджер @manager_username, в работе @employee_username и
@member_username.»

1. Resource/action: `groups` / `create`.
2. Extracted: title `Клиент / проект`, `--planfix-task-id 123456`,
   `--admin @manager_username`, `--member @employee_username`,
   `--member @member_username`. Folder defaults to the configured
   `Planfix clients`.
3. Run `telegram-planfix-assistant health` if not yet done.
4. Dry-run:

   ```bash
   telegram-planfix-assistant groups create \
     --title "Клиент / проект" \
     --planfix-task-id 123456 \
     --admin @manager_username \
     --member @employee_username \
     --member @member_username \
     --dry-run
   ```

5. Show plan: title, folder (`Planfix clients`), admins, members,
   reserve accounts from `resolved`, and `planned_actions` (create
   group, add each member, promote each admin, create invite link,
   place into folder, send `/task 123456` if `@planfix_bot` is in
   planned members).
6. Wait for «да» / «выполни». Re-run the same command without
   `--dry-run`.

### `groups set-layout`

Request: «Переключи топики чата -1003911170598 на tabs.»

1. Resource/action: `groups` / `set-layout`.
2. Extracted: `--chat-id -1003911170598`, `--layout tabs`. If the human
   does not name a layout, fall back to
   `telegram.defaults.topics_layout` and surface that choice in the plan.
3. Run `telegram-planfix-assistant health` if not yet done.
4. Dry-run:

   ```bash
   telegram-planfix-assistant groups set-layout \
     --chat-id -1003911170598 \
     --layout tabs \
     --dry-run
   ```

5. Show resolved chat id, target layout, layout source
   (`cli` vs `config`), and the single planned action
   (`set topics layout to 'tabs' for chat -1003911170598`).
6. Wait for «да» / «выполни», then re-run the same command without
   `--dry-run`. On `needs_review` (FLOOD_WAIT) point the human at
   `operations retry`; do not auto-retry.

### `groups get-layout`

Request: «Какой layout у чата -1003915612716?»

1. Resource/action: `groups` / `get-layout`. No dry-run, no
   confirmation.
2. Run:

   ```bash
   telegram-planfix-assistant groups get-layout \
     --chat-id -1003915612716
   ```

3. Return the single-word output (`list` or `tabs`) verbatim. If the
   CLI exits non-zero, surface the message and stop.

### `topics create`

Request: «Создай топик "Документы" в чате Клиент / проект.»

1. Resource/action: `topics` / `create`.
2. Extracted: `--topic-name "Документы"`, `--chat-name "Клиент / проект"`.
   Folder defaults to `Planfix clients`.
3. Dry-run:

   ```bash
   telegram-planfix-assistant topics create \
     --topic-name "Документы" \
     --chat-name "Клиент / проект" \
     --dry-run
   ```

4. Show resolved chat id, planned actions (create topic, send first
   message). If `existing_topic_ids` is non-empty, surface the warning
   and ask the human whether to continue.
5. Wait for confirmation, then run without `--dry-run`.

### `topics bulk-create`

Request: «Заведи в чате Клиент / проект топики "Документы" и "Оплата".»

1. Resource/action: `topics` / `bulk-create`.
2. Prepare `/tmp/telegram-planfix-assistant-topics.csv`:

   ```csv
   planfix_task_id,topic_name,message
   ,Документы,
   ,Оплата,
   ```

   Show the path and the contents in the plan.
3. Dry-run:

   ```bash
   telegram-planfix-assistant topics bulk-create \
     --chat-name "Клиент / проект" \
     --file /tmp/telegram-planfix-assistant-topics.csv \
     --dry-run
   ```

4. Show `items_count`, planned actions, and any warnings about
   duplicates or already-existing topic names.
5. Wait for confirmation, then re-run without `--dry-run`. Reuse the
   same `--operation-id` only if the human asks to resume a previous
   batch.

### `topics close`

Request: «Закрой топик "Документы" в чате Клиент / проект.»

1. Resource/action: `topics` / `close`.
2. Dry-run:

   ```bash
   telegram-planfix-assistant topics close \
     --chat-name "Клиент / проект" \
     --topic-name "Документы" \
     --dry-run
   ```

3. Show resolved `telegram_topic_id`, `already_closed`. If the topic is
   already closed, repeat that and ask the human whether they still
   want to run the no-op replay.
4. Otherwise wait for explicit confirmation and run without
   `--dry-run`.

### `members bulk-add`

Request: «Добавь @employee_username и @member_username в чат
Клиент / проект, @manager_username — админом.»

1. Resource/action: `members` / `bulk-add`.
2. Prepare `/tmp/telegram-planfix-assistant-users.csv`:

   ```csv
   user,role
   @employee_username,member
   @member_username,member
   @manager_username,admin
   ```

3. Dry-run:

   ```bash
   telegram-planfix-assistant members bulk-add \
     --chat-name "Клиент / проект" \
     --file /tmp/telegram-planfix-assistant-users.csv \
     --dry-run
   ```

4. Show planned actions, users already in the chat, users the dry-run
   says cannot be added.
5. Wait for confirmation. Real run uses the same command without
   `--dry-run`.

### `members bulk-remove`

Request: «Убери @employee_username из чата Клиент / проект.»

1. Resource/action: `members` / `bulk-remove`.
2. Always start with `--dry-run`. For more than one user, write
   `/tmp/telegram-planfix-assistant-users.csv` with one user per line.
   For a single user, an inline `--user @employee_username` is fine.
3. Dry-run:

   ```bash
   telegram-planfix-assistant members bulk-remove \
     --chat-name "Клиент / проект" \
     --user @employee_username \
     --dry-run
   ```

4. Show every user the dry-run would touch. If any user is in the
   protected set (configured reserve accounts or `@planfix_bot`), name
   them, refuse to add `--force` on initiative, and ask whether to
   proceed.
5. After explicit confirmation, run without `--dry-run` and with
   `--yes`. Only add `--force` when the human approves it for the
   specific protected users named in the plan.

### `messages send` — targeted

Request: «Отправь /task 123456 в топик "Документы" чата
Клиент / проект.»

1. Resource/action: `messages` / `send`.
2. Dry-run:

   ```bash
   telegram-planfix-assistant messages send \
     --text "/task 123456" \
     --chat-name "Клиент / проект" \
     --topic-name "Документы" \
     --dry-run
   ```

3. Show resolved chat id, topic id, planned action.
4. Wait for confirmation, then re-run without `--dry-run`.

### `messages send` — mass mode

Request: «Напомни во всех чатах Planfix clients в топике "Оплата"
прислать акт.»

1. Resource/action: `messages` / `send`. Warn the human up front that
   this fans out to every chat in the folder.
2. Dry-run:

   ```bash
   telegram-planfix-assistant messages send \
     --text "Пришлите, пожалуйста, акт сверки" \
     --topic-name "Оплата" \
     --mass \
     --dry-run
   ```

3. Show the full per-chat table from the dry-run: which chats will
   receive the message, which will be skipped (`topic_not_found`,
   `topic_ambiguous`, `list_topics_failed: ...`) and how many sends
   would actually happen.
4. Require an explicit confirmation that names the chat count before
   re-running without `--dry-run`.

### `folders inspect`

Request: «Покажи чаты в папке Planfix clients.»

1. Resource/action: `folders` / `inspect`. No dry-run, no confirmation.
2. Run:

   ```bash
   telegram-planfix-assistant folders inspect \
     --folder-name "Planfix clients"
   ```

3. Return the list of chats verbatim.

### `folders add-chat`

Request: «Перенеси чат Клиент / проект в папку Planfix clients.»

1. Resource/action: `folders` / `add-chat`.
2. Dry-run:

   ```bash
   telegram-planfix-assistant folders add-chat \
     --folder-name "Planfix clients" \
     --chat-name "Клиент / проект" \
     --dry-run
   ```

3. Show `folder_id`, resolved chat, and `already_in_folder`. If the
   chat is already there, restate that and skip the real run unless
   the human insists.
4. Otherwise wait for confirmation, then run without `--dry-run`.

### `operations status`

Request: «Что с операцией op_2026_05_19_abcd?»

1. Resource/action: `operations` / `status`. No dry-run, no
   confirmation.
2. Run:

   ```bash
   telegram-planfix-assistant operations status \
     --operation-id op_2026_05_19_abcd
   ```

3. Return the per-status counts and any failing items.

### `operations retry`

Request: «Повтори операцию op_2026_05_19_abcd.»

1. Resource/action: `operations` / `retry`. First run `operations
   status` to show the human the current state.
2. Dry-run:

   ```bash
   telegram-planfix-assistant operations retry \
     --operation-id op_2026_05_19_abcd \
     --dry-run
   ```

3. Show `would_reset_operation`, the list of items that would be
   reset, and any "no-op" warning.
4. Wait for confirmation, then re-run without `--dry-run`.

### `auth`

Request: «Перелогинь технический аккаунт.»

1. Resource/action: `auth` / `login`. The agent does not run this.
2. Tell the human to run `telegram-planfix-assistant auth` themselves
   in a terminal where they can enter the phone, the code, and the
   optional 2FA password. The agent never asks for these values in
   chat and never relays them.
3. Once the human confirms the relogin is done, the agent re-runs
   `health` before any further state-changing command.

## When the agent must stop and ask

The agent stops and asks the human in any of the following situations.
"Stop" means: do not run another command, do not guess, do not retry.
Reuse the short templates from "Clarification templates" below.

- The request maps to no entry in the Resources & actions table — the
  resource/action is unclear or the request asks for something the CLI
  cannot do.
- A required parameter is missing: no username for `members bulk-add`
  / `members bulk-remove`, no chat reference for any chat-scoped
  command, no topic for `topics create` / `topics close`, no text for
  `messages send`, no `--operation-id` for `operations status` /
  `operations retry`.
- A lookup is ambiguous: more than one chat matches the title, more
  than one topic matches the name, more than one user matches the
  alias.
- `telegram-planfix-assistant health` reported any non-`ok` field or
  a non-zero exit. Surface the message verbatim; do not run anything
  else first.
- `--dry-run` returned an error or `status != dry_run`. Show the
  error to the human and ask before retrying or changing parameters.
- The dry-run plan touches a protected account (configured reserve
  admins / reserve members or `@planfix_bot`) — never add `--force`
  on the agent's own initiative.
- The human asks for an action that is not in the resource/action
  table (writing a new bot, calling Telethon directly, editing
  `data/config.yml`, etc.). Decline and ask whether the CLI flow
  covers what they need.
- The request implies the agent should run `auth` itself, or collect
  a phone, code or 2FA password from the chat.

## Clarification templates

Keep clarifications short and direct. One question per turn, no
preamble, no apologies. Plain Russian, no bureaucratese.

- Missing username:
  «Не вижу username, кого добавить?»
- Missing chat:
  «В каком чате это сделать? Назови чат или укажи `--chat-id`.»
- Missing topic:
  «В каком топике? Укажи название топика.»
- Missing message text:
  «Какой текст отправить?»
- Missing operation id:
  «Какая операция? Пришли её id.»
- Ambiguous chat:
  «Нашёл несколько чатов с похожим названием, какой выбрать?»
- Ambiguous topic:
  «В этом чате несколько топиков с таким названием. Какой именно?»
- Ambiguous user:
  «Этому имени соответствует несколько аккаунтов, какой именно?»
- Health is not ok:
  «`health` показал проблему: <сообщение>. Что делаем?»
- Dry-run failed:
  «dry-run упал: <сообщение>. Проверь название/параметры?»
- Protected account touched:
  «В плане затронут технический аккаунт <имя>. Добавить `--force`
  и продолжить?»
- Action is out of scope:
  «Этого в CLI нет. Подойдёт <ближайшая команда> или нужно ручное
  действие?»

## What is out of scope

The skill describes how to drive the existing CLI. It does not
authorise the agent to:

- write another Telegram bot or any new long-running service;
- stand up a new HTTP API, webhook receiver, or worker;
- add Planfix-side automation (Planfix scenarios, webhooks, custom
  fields) — the project handles Planfix elsewhere;
- import `telethon` or talk to Telegram MTProto directly;
- call the project's HTTP API from inside the skill — the CLI is the
  only entry point;
- change Telegram state without a confirmed plan;
- guess a chat, topic, or user when the match is not exact — always
  ask;
- bypass `--dry-run` for any state-changing command that supports it;
- remove protected accounts (reserve admins / reserve members /
  `@planfix_bot`) without an explicit human ask and `--force`;
- collect phones, login codes, or 2FA passwords in chat — `auth` is
  always run by a human in a terminal;
- use real client names, real usernames, or real invite links in any
  example, plan, or log line — only the anonymized identifiers
  documented under "Scenarios".
