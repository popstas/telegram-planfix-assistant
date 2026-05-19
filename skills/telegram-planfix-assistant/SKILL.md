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

1. **Read-only** — `health`, `folders inspect`, `operations status`. Run them
   immediately, no confirmation, no `--dry-run`.
2. **State-changing, single object** — `groups create`, `topics create`,
   `topics close`, `messages send` (single chat), `folders add-chat`,
   `operations retry`. Always: prepare command → run with `--dry-run` →
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
   `topics create`, `topics bulk-create`, `topics close`,
   `members bulk-add`, `members bulk-remove`, `messages send`,
   `folders add-chat`, `operations retry`.
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
