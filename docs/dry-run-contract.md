# `--dry-run` contract for CLI commands

This file documents the audit done in Task 1 of
`docs/plans/20260519-telegram-skill-and-dry-run.md` and the shared response
shape that all mutating CLI commands must follow when invoked with
`--dry-run`.

## Audit — which CLI commands need `--dry-run`

Read-only commands — `--dry-run` is **not added** and must not appear:

- `auth` — interactive, only meaningful when actually changing state.
- `health` — read-only.
- `version` — read-only.
- `folders inspect` — read-only.
- `operations status` — read-only.

Mutating commands — `--dry-run` is required and must follow the contract
below:

| Command | Current state of `--dry-run` | What dry-run must validate |
|---|---|---|
| `groups create` | missing | title, folder, admins, members, reserve accounts, can group be created, can invite link be issued |
| `topics create` | missing | chat, folder, topic name, optional `planfix_task_id`, optional first message |
| `topics bulk-create` | missing | chat, folder, items parsed from CSV/JSON, duplicate detection by `topic_name`/`planfix_task_id` |
| `topics close` | missing | chat, folder, topic, current state of topic |
| `members bulk-add` | missing | chat, folder, parsed items, current membership, who can't be added |
| `members bulk-remove` | present (preview only) | chat, folder, parsed items, who is/isn't in the chat, protected accounts |
| `messages send` | missing | chat/folder/topic, text, mass mode flag, planned recipients |
| `folders add-chat` | missing | folder, chat, whether chat is already in folder |
| `operations retry` | missing | operation id, current status, whether retry is safe |

## Already-validated invariants

Validations the existing code already performs and that dry-run must reuse
without divergence:

- `groups create` validates non-empty title (`GroupCreateRequest.__post_init__`),
  required idempotency key (planfix_task_id or title), folder defaults via
  `_resolve_folder_name`.
- `topics create` validates non-empty topic name and chat id /
  chat-name-within-folder via `resolve_chat_in_folder`.
- `topics bulk-create` parses CSV/JSON via
  `_parse_bulk_topics_csv` / `_parse_bulk_topics_json` which already reject
  empty `topic_name`.
- `members bulk-add` parses CSV/JSON via
  `_parse_bulk_members_csv` / `_parse_bulk_members_json`.
- `members bulk-remove` already normalizes user references and guards
  protected accounts (`protected_user_set` + `--force`).
- `messages send` validates targeted vs mass mode mutually-exclusive flags.
- `folders add-chat` already resolves a chat in folder via
  `resolve_chat_in_folder` and surfaces ambiguous matches.
- `operations retry` already rejects retry of completed operations and
  reports which items get reset.

Dry-run must produce the **same** validation errors and exit codes as the
real run for these inputs — operators rely on dry-run as a safe rehearsal.

## Protected accounts (require `--force` to remove)

Source: `members.service.protected_user_set(config=telegram_config)`.

The set always contains:

1. `@planfix_bot` (constant `members.service.PLANFIX_BOT_USERNAME`).
2. All `telegram.reserve_admins` from `data/config.yml`.
3. All `telegram.reserve_members` from `data/config.yml`.

Each reference is normalized through `members.service.normalize_user_ref`
before comparison so case / whitespace variants of the same identity are
caught.

Removing any of these requires explicit `--force` on `members bulk-remove`
**and** explicit human confirmation in the agent skill. This rule must
also be honored by dry-run: the response must mark such items
`protected: true` so reviewers see the risk before they re-run without
`--dry-run`.

## Shared dry-run response shape

All mutating commands, when invoked with `--dry-run`, must print a single
JSON document to stdout containing at least these fields:

```json
{
  "status": "dry_run",
  "dry_run": true,
  "command": "<resource>.<action>",
  "would": "<short imperative summary of the intended change>",
  "resolved": {
    "...command-specific identifiers found during validation..."
  },
  "planned_actions": [
    "...one human-readable line per side effect that real run would do..."
  ],
  "warnings": []
}
```

Rules:

- `status` is always the literal string `"dry_run"`. Real runs never use
  this value.
- `dry_run: true` is kept for backwards compatibility with the existing
  `members bulk-remove` consumers and any tooling that already greps for
  it.
- `command` uses dotted form, e.g. `"groups.create"`,
  `"topics.bulk_create"`, `"members.bulk_remove"`.
- `resolved` is an object — its keys depend on the command (chat id, topic
  id, folder name, parsed items, etc.) but must contain only data the
  command actually resolved during validation.
- `planned_actions` is a list of strings; each entry must be safe to show
  to a human operator without revealing real usernames or invite links
  that should not leak.
- `warnings` is a list of strings — non-fatal observations such as
  "user already in chat" or "topic already closed" that operators should
  read before approving the real run.
- Validation **errors** are returned the same way as the real run
  (non-zero exit code + stderr message). Dry-run does not silently
  swallow errors.
- Dry-run must not perform any Telegram side effect, must not create a
  persistent operation record, must not enqueue worker jobs, and must
  not write to the message store.
- Idempotency-key conflicts that would result in a "already done" path on
  the real run must be reflected as a `planned_actions` entry / warning
  rather than as an error.

The shared shape is enforced by helpers in
`tests/test_dry_run_contract.py` (see `assert_dry_run_envelope`) so each
command's own test file can call into it instead of re-defining the
contract.
