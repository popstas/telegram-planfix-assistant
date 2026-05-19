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
uvicorn telegram_planfix_assistant.http_api.app:create_app --factory --port 8085
```

## Configuration

Config is read from `data/config.yml` by default. The `data/` directory is excluded from version control and holds the Telethon session, SQLite database, and secrets.

If `./data/config.yml` is absent, the loader falls back to `~/.config/telegram-planfix-assistant/config.yml`. On a clean machine, running any CLI command without `--config` will create a template at that path with `REPLACE_ME` placeholders for `api_id`, `api_hash`, and `bearer_token` — fill them in and re-run.

To reach Telegram through a proxy, set `telegram.proxy_url` to a single URL — supported schemes are `socks5`, `socks4`, `http`, and `https`. Credentials and explicit ports are optional:

```yaml
telegram:
  proxy_url: "socks5://user:pass@host:1080"   # or http://host:8080, socks4://host, ...
```

Leave it unset (or remove the line) to connect directly.

See `docs/plans/20260518-telegram-planfix-assistant-mvp.md` for the full configuration schema and feature scope.

## Docker

The service ships as a slim Python 3.12 image. Runtime state (Telethon session, SQLite database, `data/config.yml`, bearer token) lives in `/data`, which must be mounted as a volume — nothing sensitive is baked into the image.

Build and run with `docker compose`:

```bash
mkdir -p data
cp path/to/your/config.yml data/config.yml   # fill in api_id, api_hash, bearer_token, etc.
docker compose up -d
curl http://127.0.0.1:8085/health
```

Run a one-shot CLI invocation against the same volume:

```bash
docker compose run --rm telegram-planfix-assistant \
    telegram-planfix-assistant health
```

The `auth` CLI is interactive (it prompts for phone, code, and optional 2FA password), so run it with a TTY attached:

```bash
docker compose run --rm -it telegram-planfix-assistant \
    telegram-planfix-assistant auth
```

The Telethon session is written to `/data` and persists across container restarts.

A self-contained smoke script lives at `scripts/docker-smoke.sh`. It builds the image, starts a throwaway container with a temporary `data/config.yml`, polls `GET /health` until it returns `200`, and tears everything down.

```bash
bash scripts/docker-smoke.sh
```

## Tests

```bash
pytest
```
