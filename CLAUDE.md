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
  - `tracker/`: the owners' private bet tracker, parlaytracker folded in
    ([docs/plans/integrate-parlaytracker.md](docs/plans/integrate-parlaytracker.md)).
  - The live chat (live odds on demand, [docs/plans/live-chat.md](docs/plans/live-chat.md)):
    `core/livechat.py` (rules), `ingest/live_odds.py` (the cache and the credits),
    `api/live_chat.py` (its routes). `core/live.py` is the live scores, a different thing.
- `web/`: Next.js, the only public surface. It proxies `/api/*` to the backend.
- `docs/`: GOALS, licensing record, deploy runbook, and `plans/` (the tracker's phases).
- `deploy/laptop/`: the private preview on the owner's own machine (the `deploy-laptop` skill).

## Commands

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,localdb]"
export TEST_DATABASE_URL="$(.venv/bin/python scripts/dev_postgres.py)"   # local Postgres, no Docker
.venv/bin/ruff check . && .venv/bin/pytest

cd ../web && npm ci && npm run lint && npm run typecheck && npm run build
npm run e2e          # Playwright; see web/README.md for the seeded backend it needs
scripts/visual.sh    # visual snapshots, in Playwright's container image (web/README.md)
```

In the Claude Code cloud environment, set `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt` for pip and
for any live HTTP call through the proxy.

## Rules that are easy to break

- **Never invent a number.** Every displayed price comes from a stored `Price` row and its
  snapshot. Never interpolate between snapshots. LLM output is only matched onto IDs; it never
  supplies a displayed number.
- **Requests from users never reach a data provider** (ESPN, The Odds API, nflverse). Only the
  worker does. Two exceptions, both in the API: the optional LLM fallback, and the live chat
  (`api/live_chat.py`, 2026-11-20), whose questions reach The Odds API through
  `ingest/live_odds.py`, and only there.
- **Licensing** (docs/licensing.md): no endpoint may list prices across players or games, or
  export raw odds. Two exceptions, both the owner's calls:
  - 2026-09-30: a same-game parlay card shows the price of each of the 2 to 6 legs someone
    picked, in one game, at one book (never the other books).
  - 2026-10-17: an owner's own recaps and history under `/me` (`api/me.py`), behind their own
    sign-in, never cached or shared.
  - 2026-11-20: the live chat shows one player's or one team's live lines in one game, or the 2
    to 6 legs of a parlay someone named. A question with no player or team is refused; the
    cached reply from The Odds API is never returned or listed.

  The backend has no public address.
- **Secrets:** never print, log or commit API keys. `Settings` holds them as `SecretStr`. httpx
  loggers stay at WARNING, because the Odds API key is a query parameter.
- **Tests:** no test uses the network (`tests/conftest.py` blocks it); use `tests/fixtures/`.
  Odds API fixtures cost credits, so reuse them and ask before recording new ones.
- **Ported files** keep their `Ported from parlaytracker@c3bd43c …` header. Say what changed.
- **Migrations:** after a new Alembic migration, read the generated file and run
  `tests/db/test_migrations.py`.
- **The tracker:** slips and legs are written only through `juju/tracker/services.py`, and the
  owners' saved lookups only through `juju/tracker/saved.py`. Services
  flush; callers commit. Its data never reaches a public response, a share image or
  `/api/status`.
- **Colours:** only `web/lib/tokens.ts` names one (the look of 2026-11-11). Text with no panel
  under it is `var(--text)`; `npm test` and `e2e/audit.spec.ts` hold both.
- **The live chat** (docs/plans/live-chat.md): live prices only, never an archived `Price`, a
  snapshot or `core/t45.py` (`tests/db/test_live_chat.py` checks the imports). Odds are cached
  for at most 60 seconds (`LIVE_CACHE_SECONDS` can only shorten it); nothing prefetches, and a
  repeat question within the window makes no call. The credits shown are The Odds API's own
  count, never a sum of ours. The reply is words from templates and numbers from the server; a
  model, when there is one, never supplies a displayed number. It is off at the site unless
  `SITE_PASSCODE` is set.
- **Suggested bets** (quick picks) carry no price and are never prefetched: a price is first
  asked for when one is opened.
- **Workers:** exactly one worker runs (Postgres advisory lock), and it never scales to zero.
