# Juju

A public site showing what a $10 bet placed 45 minutes before kickoff (T-45) would have paid
for a recent NFL play. **Read [docs/GOALS.md](docs/GOALS.md) first** (the decisions and the reasons
for them), then [HANDOVER.md](HANDOVER.md) (where the build stands and what to do next). Update
HANDOVER.md before you finish a session.

## Layout

- `backend/`: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16. The package is `juju/`:
  - `core/`: pure rules.
  - `ingest/`: providers and parsing.
  - `worker/`: the only process that calls providers.
  - `api/`: internal HTTP, reached only through `web/`.
- `web/`: Next.js, the only public surface. It proxies `/api/*` to the backend.
- `docs/`: GOALS, licensing record, deploy runbook.

## Commands

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,localdb]"
export TEST_DATABASE_URL="$(.venv/bin/python scripts/dev_postgres.py)"   # local Postgres, no Docker
.venv/bin/ruff check . && .venv/bin/pytest

cd ../web && npm ci && npm run lint && npm run typecheck && npm run build
npm run e2e          # Playwright; see web/README.md for the seeded backend it needs
```

In the Claude Code cloud environment, set `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt` for pip and
for any live HTTP call through the proxy.

## Rules that are easy to break

- **Never invent a number.** Every displayed price comes from a stored `Price` row and its
  snapshot. Never interpolate between snapshots. LLM output is only matched onto IDs; it never
  supplies a displayed number.
- **Requests from users never reach a data provider** (ESPN, The Odds API, nflverse). Only the
  worker does. The optional LLM fallback is the one outbound call the API makes.
- **Licensing** (docs/licensing.md): no endpoint may list prices across players or games, or
  export raw odds. The backend has no public address.
- **Secrets:** never print, log or commit API keys. `Settings` holds them as `SecretStr`. httpx
  loggers stay at WARNING, because the Odds API key is a query parameter.
- **Tests:** no test uses the network (`tests/conftest.py` blocks it); use `tests/fixtures/`.
  Odds API fixtures cost credits, so reuse them and ask before recording new ones.
- **Ported files** keep their `Ported from parlaytracker@c3bd43c …` header. Say what changed.
- **Migrations:** after a new Alembic migration, read the generated file and run
  `tests/db/test_migrations.py`.
- **Workers:** exactly one worker runs (Postgres advisory lock), and it never scales to zero.
