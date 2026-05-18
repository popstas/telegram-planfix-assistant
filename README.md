# telegram-planfix-assistant

Telegram automation service for the Planfix ↔ Telegram integration.

Three interfaces share one domain layer:

- HTTP API (FastAPI) on port `8085` with bearer-token auth — primary entry point for Planfix and automations.
- CLI (`telegram-planfix-assistant`) — mirrors every HTTP endpoint plus admin commands (`auth`, `operations status`, `operations retry`).
- Worker/queue — performs Telegram operations with throttling and `FLOOD_WAIT` handling.

Runs on MTProto via Telethon under a technical Telegram user account.

## Quick start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Place a config file at data/config.yml (see Configuration below)
telegram-planfix-assistant auth      # interactive Telethon login
telegram-planfix-assistant health    # show current health
uvicorn telegram_planfix_assistant.http.app:create_app --factory --port 8085
```

## Configuration

Config is read from `data/config.yml` by default. The `data/` directory is excluded from version control and holds the Telethon session, SQLite database, and secrets.

See `docs/plans/20260518-telegram-planfix-assistant-mvp.md` for the full configuration schema and feature scope.

## Tests

```bash
pytest
```
