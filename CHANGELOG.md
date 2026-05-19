# Changelog


## Unreleased

### Features

- http: Add groups layout get/set endpoints
- groups: Apply topics_layout default after create
- cli: Add groups set-layout and get-layout commands
- groups: Add set/get topics_layout service functions
- groups: Add topics-layout backend protocol and Telethon adapter
- config: Add topics_layout default to TelegramDefaults
- telegram: Add proxy_url config option
- config: Add ~/.config fallback and bootstrap
- Verify acceptance criteria for telegram skill and dry-run
- Add error guidance, clarification templates, and scope boundaries to SKILL.md
- Document all 13 resource/action pairs and scenarios in SKILL.md
- Add SKILL.md skeleton and shared agent rules
- Add --dry-run to folders add-chat and operations retry
- Add --dry-run to members bulk-add and messages send
- Add --dry-run to groups create and topics create/bulk-create/close
- Audit CLI dry-run state and define shared envelope contract
- Run live e2e against real Telegram and harden production wiring (task 18)
- Add e2e test scaffolding (task 17)
- Verify MVP acceptance criteria (task 16)
- Add Docker image and deployment scaffolding
- Add structured logging and alert hooks
- Add send message/command HTTP + CLI with mass mode
- Add bulk remove members HTTP + CLI with dry-run and force guard
- Add bulk add members HTTP + CLI with privacy handling
- Add topic close HTTP + CLI with idempotency
- Add bulk topic create HTTP + CLI with per-item queue
- Add single topic create HTTP + CLI with first-message logic
- Add group create HTTP + CLI with idempotency and folder placement
- Add chat folder resolution and folder operations
- Add async worker queue with FLOOD_WAIT handling
- Add SQLite persistence and idempotency layer
- Add /health endpoint and health CLI with shared probes
- Add Telethon session wrapper and auth CLI
- Scaffold telegram-planfix-assistant project

### Bug Fixes

- Address codex review findings
- Address code review findings
- review: Unblock bulk-remove --dry-run for protected accounts
- review: Address code review findings

### Documentation

- Add CLAUDE.md with architecture and command reference
- Enforce e2e tests
- Mark Task 18 e2e checkboxes as not-automatable
- Add creds, repeat e2e
- Add task 17: e2e tests

### Miscellaneous

- Ignore installed skills and .claude
- Chmod 644 docs
- Drop exec bit on scripts/*.sh
- Add git-cliff changelog and GitHub Actions workflows
- Initial commit with spec and ralphex plan

