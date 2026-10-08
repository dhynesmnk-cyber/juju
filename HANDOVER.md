# Handover

For the next agent picking up Juju. **Read [docs/GOALS.md](docs/GOALS.md) first** for the
product, the owner's decisions and the reasons behind them. Then read
[CLAUDE.md](CLAUDE.md) for the commands and the rules that are easy to break. This file says
where the build stands and what to do next. Update it before you finish a session.

## Where things stand (2026-10-08)

| | |
|---|---|
| Branch | Everything is in `main`. PR #1 merged M0–M2, PR #2 M3 and M4, PR #3 the parlay leg prices, PR #4 fixes to this file, PR #5 the first-TD scorer check, PR #6 the laptop preview and its passcode gate, PR #7 the late play-by-play retry, and PR #8 the CI time limits. PR #3 recovered two commits that were pushed to `claude/happy-galileo-p7ify3` after PR #2 merged (`00befec`, cherry-picked, and what was right in `a133cd3`). No PR is open |
| CI | Green on every merged PR head. Three jobs: `backend` (ruff + pytest on Postgres 16), `web` (lint, typecheck, `npm test`, world-size check, build) and `e2e` (seeds the recorded game, 17 Playwright tests at phone width, with Cloudflare's public Turnstile test keys). Each has a time limit of about five times its slowest green run (15, 10 and 20 minutes), so a hung runner fails in minutes, not after GitHub's six hours: on 2026-10-08 an e2e job hung for 18 minutes in `apt-get` (`playwright install --with-deps`), and one re-run passed |
| Tests | 1,164 backend tests, 9 web unit tests (`node --test`), 17 Playwright tests |
| Deployed | **A private preview, run by the owner** on their own Ubuntu laptop (`deploy/laptop/`, the `deploy-laptop` skill): Docker Compose, Tailscale Funnel, one shared passcode, the recorded demo game. It was deployed end to end in a cloud container on 2026-10-07, and the owner has it running on the laptop (2026-10-08). Fly.io (`docs/deploy.md`) is still the launch plan; no Fly apps exist |
| Real data | No real capture has run. The archive has only been exercised on recorded fixtures. nflverse (free) was read for real, to record its fixtures |
| Milestones | M0–M4 are done. M5 (launch hardening) is next: part of it is engineering you can do now, part needs the owner |

## First thing to do

1. **Set up the environment** (below), and run the tests before changing anything.
2. **Pick from "Next steps"** in order, unless the owner asks for something else. A merged PR
   is finished: start each new piece of work from the latest `main`, and give it a new PR.
3. **Before you finish, check that everything you pushed reached `main`** (or sits in an open
   PR). Never push to a branch whose PR has merged: that is how `00befec` was stranded.

## Setting up a fresh container

```bash
cd backend
python3.12 -m venv .venv && SSL_CERT_FILE=/root/.ccr/ca-bundle.crt .venv/bin/pip install -e ".[dev,localdb]"
export TEST_DATABASE_URL="$(.venv/bin/python scripts/dev_postgres.py)"   # creates juju_test
.venv/bin/ruff check . && .venv/bin/pytest

# A second database for the seeded demo (juju_test is wiped by the tests):
.venv/bin/python -c "
from pathlib import Path; import pgserver
s = pgserver.get_server(Path('.pgdata'), cleanup_mode=None)
if '(1 row)' not in s.psql(\"SELECT 1 FROM pg_database WHERE datname = 'juju_dev';\"): s.psql('CREATE DATABASE juju_dev;')
print(s.get_uri('juju_dev'))"
export DATABASE_URL=<the URL it printed>
.venv/bin/alembic upgrade head && .venv/bin/python -m juju.cli seed live

cd ../web && NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt npm ci
npm run lint && npm run typecheck && npm test && npm run build
```

Then run the stack and e2e as in "Gotchas" below, and `web/README.md`.

## What exists

**Backend** (`backend/juju/`):
- `core/`: pure rules, no I/O:
  - `markets.py`: the catalog of 34 Odds API market keys. A real call accepted all 34 (no
    422), and 29 came back priced. Never yet seen with a price: sacks, tackles and assists,
    defensive interceptions, rush TDs and reception TDs;
  - `t45.py`: which archived price a card shows;
  - `card.py`: the outcome state machine and the money (a bet with no line is never decided);
  - `plays.py`: card ordering;
  - `odds.py`, `settlement.py` and `live.py`, ported from parlaytracker;
  - `parlay.py` (M4): legs in a URL, one book for every leg when one priced them all, the
    parlay outcome (a lost leg loses it, pushes drop out, no stat line leaves it undecided);
  - `models.py`, including `StatCorrection` and `DecidingPlay` (migrations 0002, 0003), and
    `Game.first_td_official` (0004) and `plays_checked_at` (0004, renamed in 0005).
- `ingest/`:
  - `odds_api.py`: live, historical and scores endpoints;
  - `espn.py`: box score parsing, plus notable plays for the chips;
  - `nflverse.py` (M3): nflverse's release CSVs (schedule, players, weekly stats,
    play-by-play) over Juju's HTTP client, streamed and filtered: no polars, the worker has
    512 MB. `STAT_COLUMNS` lists only the stats that matched ESPN for all 61 players of the
    recorded game; `play_value` gives each play's share of a stat;
  - `router.py`, `guards.py`: circuit breakers, failover and integrity checks, ported;
  - `resolve.py`: name matching; `parse.py`: the deterministic free-text reader;
  - `llm.py`: the fallback, off until `LLM_API_KEY` and `LLM_MODEL` are set.
- `worker/`: the only process that calls providers:
  - `capture.py`, `schedule.py`, `archive.py`, `jobs.py`: the archive (M1);
  - `live.py`: the live loop. `write_box` records corrections after settling and never
    overwrites a value nflverse supplied; each read also notes line crossings;
  - `verify.py` (M3): `VerifyGames`, 10:07 and 16:07 ET and at startup. It checks last week's
    final games against nflverse: agreeing stats are marked verified, a disagreeing one is
    replaced by nflverse's and recorded in `stat_corrections`, a disputed final score applies
    nothing (`games.last_error`). Then it reads the play-by-play: the exact deciding plays,
    and the longest-play and first-TD checks. A game not in it yet is read on a later run
    (`games.plays_checked_at` still unset) until the game is a week old;
  - `deciding.py` (M3): the play that decided a bet. Live: "on or around", the latest notable
    play by that player among the plays in the read that saw the stat pass the line (only the
    game clock if there is none, or on a game's first read). Next day: exact, from the
    play-by-play, kept only if the play-by-play adds up to the final stat. `check_longest`
    confirms the longest rush and catch from the play-by-play, and `check_first_td` records
    its first touchdown's scorer for the cards to compare with ESPN's (neither ever corrects);
  - `__main__.py`: the scheduler, holding an advisory lock.
- `api/`: FastAPI, private and reached only through the site:
  - `app.py`: the endpoints. Lookups: 30 a minute per client, and after 12 in 15 minutes a
    428 Turnstile challenge when the site has Turnstile on. `GET /api/parlay/{game}?legs=`
    takes 2 to 6 legs;
  - `views.py`: cards with `verified`, `decided_by` and specific correction notes; and
    `parlay_view` (M4), which prices legs exactly like single cards and shows each leg's price;
  - `lookup.py`: which player or team a lookup means; reads the play from the named player's
    side (a defender's interception is his); several certain bets in one line are a parlay.
- `cli.py`: `backfill`, `unmapped`, `verify` (runs the nflverse check now), `seed live|final`.
- `scripts/`: `dev_postgres.py`; `record_nflverse.py` (slices nflverse files into fixtures,
  free); `eval_lookup.py` (the golden set with or without an LLM, to choose `LLM_MODEL`).

**Web** (`web/`, Next.js 16):
- Pages: `/`, `/g/[game]/[player]`, `/g/[game]/team/[team]`, `/g/[game]/parlay?legs=` (M4)
  and the info pages. Result pages take `?card=` to put a shared card first.
- `app/api/[...path]/route.ts` is the **only** way into the backend: an allowlist of paths.
  It also verifies Turnstile tokens (`lib/turnstile.ts`) and sets a signed one-hour cookie.
- `.../image/route.tsx` under both result pages (M3): the share image, one card in its world
  with the Jujus (`lib/shareImage.tsx`, next/og). Pages emit Open Graph tags for it.
- `components/`: `ResultCard` (Verified chip, deciding play, Share and Add to parlay),
  `ResultView`, `ParlayTray` (per game, in the viewer's browser: `lib/parlayTray.ts`),
  `ParlayView` (a light in the sky per leg, each leg's price; gold only when all are lit),
  `SearchBox` (shows `TurnstileWidget` only when challenged), `world/` (option A: shader,
  Jujus).
- `proxy.ts` (Next 16's middleware): the passcode gate, only when `SITE_PASSCODE` is set
  (`lib/passcode.ts`, `lib/unlockPage.ts`, `app/unlock/check/route.ts`). A locked page gets a
  plain HTML form at its own address; an API call gets a JSON 401. `lib/signedPass.ts` signs
  both this cookie and Turnstile's.

**Deploy**: `deploy/laptop/` (compose, `up.sh`, the owner's README) for the private preview;
`backend/fly.toml`, `web/fly.toml` and `docs/deploy.md` for launch.
`.claude/skills/deploy-laptop/SKILL.md` explains the laptop setup, its invariants and its
failure modes, and how to test it in a cloud container.

**Docs**: `docs/GOALS.md` (v2: decisions, milestones), `docs/licensing.md`, `docs/deploy.md`
(Fly, Cloudflare including Turnstile and the share-image cache, the worlds kill switch, the
verification job, alerts), `docs/GOALS-v1-brainstorm.md` (unchanged).

## Decisions the owner made (don't reopen them)

1. **Payouts:** $10 at T-45, actual result first, fair value alongside.
2. **Prices:** self-archived real prices. Never invent or interpolate one.
3. **Input:** free text, player typeahead and tappable plays. The confirmation card appears only
   when the match is unsure.
4. **Scope:** player props, game lines and same-game parlays.
5. **Book order:** Hard Rock Bet, then DraftKings, then the others (`BOOKS` setting).
6. **Hosting:** Fly.io in `iad`, with Cloudflare in front.
7. **Odds vendor:** The Odds API. Its terms allow a user-facing commercial app but not
   redistributing the raw data. So no endpoint may list or export prices across players or
   games, and the backend has no public address.
8. **Look:** option A, a shader world that follows the bet's state, plus the Jujus. The owner
   chose it from the mockup at https://claude.ai/artifact/E1syoBM4LP14DMPjKiLy73, which is
   private to them.
9. **Parlay leg prices are shown** (2026-09-30, GOALS decision 10): the one exception to 7. A
   parlay card lists each leg's price at the parlay's book, exactly as that leg's own card
   shows it, and only for the 2 to 6 legs someone picked, in one game; never the other books.
   `tests/db/test_parlay.py` holds it to that.
10. **The preview runs on the owner's laptop** (2026-10-07), not Netlify (it can't run the
    Python backend) and not yet Fly (cost): for the owner and two users, behind one shared
    passcode, through their existing Tailscale, with the recorded demo game and no Odds API
    key. The owner runs `deploy/laptop/up.sh` themselves.

## Decisions made by agents (reasons in the commits; change only with a reason)

- **A disagreement with nflverse re-opens the payout** with the official number, and the card
  says what changed. That follows GOALS v1 section 9 ("a settled payout can be re-opened and
  re-labelled if the official stat changes"); parlaytracker sent it to a person instead, and
  Juju has no one to review. A disputed *final score* is never applied.
- **Only stats that matched ESPN exactly on a real game are checked.** A mapping that drifts
  would "correct" good numbers. `tests/unit/test_nflverse.py` pins the agreement.
- **The first touchdown from the play-by-play only confirms**, like the longest plays: ESPN's
  scorer decides the card, and a disagreement leaves it unverified, never corrected. The
  play-by-play is a derived source, not an official stat column, and flipping a first-TD bet
  flips it for everyone who looked it up.
- **A first name alone is offered, never picked** ("Will", "Chance", "Case" are players here).
- **The edge rule for lookups is a block, not a managed challenge.** A challenge page can't be
  solved from a `fetch`; Turnstile runs in the page instead. (GOALS §3 and `docs/licensing.md`
  still say "Turnstile runs at Cloudflare": the widget is Cloudflare's, but the check is in
  the site's proxy.)
- **A parlay is priced at one book** when a book in the chain priced every leg (on-time first);
  otherwise each leg keeps its book and the card says so. Every parlay carries the GOALS §5
  correlation note.
- **Several bets in one line become a parlay only when every part is certain**; otherwise the
  line is read as one lookup, as before.
- **A card with no price has no line and is never decided** (before, it crashed the page).

## Fixtures (reuse them: the Odds API ones cost credits)

| File | What it is |
|---|---|
| `odds_api/nfl_event_odds_2026-09-28_PHI-CHI.json` | Real odds about 2 h before kickoff, 14 markets, 9 `us` books with no Hard Rock (5 of them offshore). From parlaytracker |
| `odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json` | **Recorded 2026-09-30 for 29 credits.** It asked for all 34 keys and all 10 books; 29 markets came back, from 8 books (no Caesars or Fanatics, which need a paid plan) |
| `espn/nfl_summary_401872963_full.json` | The full PHI @ CHI summary, used by the plays parser and the seed |
| `espn/nfl_roster_{21,3,23,5}.json`, `espn/nfl_scoreboard_*.json` | Rosters and scoreboards for both recorded games (`nfl_roster_22.json`, Arizona, is only for a parser test). ESPN is free |
| `nflverse/*.csv` | **Recorded 2026-09-30, free**: PHI @ CHI (final 27–7) and PIT @ CLE (not played) from nflverse's schedule, its 65 stat lines and players, and its 155 plays. Re-record with `scripts/record_nflverse.py` |
| `lookups/golden.jsonl` | 177 lookups about the recorded game with their right answers (`tests/lookups.py`): 125 exact, 49 asked, 3 missed, 0 wrong without the LLM |

The recorded PHI @ CHI odds have **no TD-scorer markets**; tests that need them add a price to
the recorded snapshot (see `tests/db/test_deciding.py`). The PIT @ CLE file has them.

The Odds API key in this cloud environment (`ODDS_API_KEY`) is **parlaytracker's free-tier
key**. Its quota was **500** on 2026-10-07 (the free tier's monthly allowance, reset on the
1st): nothing spent in October. Don't spend it without the owner's OK. parlaytracker's own
handover says the key should be rotated. **It is exported in this shell**, so anything that
reads the environment can pick it up: on 2026-10-07 a test run of the laptop stack started its
worker with it, and the worker made one free events call before it was stopped. That is why
`deploy/laptop/up.sh` runs Compose under `env -i` and names the key `JUJU_ODDS_API_KEY`.

## Next steps

In order. None of these needs the owner, except where it says so.

**1. Follow-ups from M3 and M4** (small, each a commit):
- **A share image for a parlay:** `/g/[game]/parlay/image?legs=`, reusing `lib/shareImage.tsx`
  (the lights in the sky, the combined payout, the correlation note), and Open Graph tags on
  the parlay page.
- **The golden set:** add parlay lines (they resolve to `kind: "parlay"`; `tests/lookups.py`
  needs a verdict for them), and consider team nicknames ("Philly", "Birds") and "game total
  over": the set's 3 misses. Raise the floors in `tests/db/test_golden.py` when the numbers
  improve.

**2. M5 engineering that can be done before deploy:**
- **k6 load test** (`load/` or `web/load/`): the cached card endpoints behind the proxy, and
  lookups at the rate limits, against the local stack. Record the results in `docs/deploy.md`.
- **Lighthouse CI** on `/`, a player page and a parlay page at phone width, as a CI job.
- **Visual snapshots** per world and per share image (Playwright `toHaveScreenshot`, with
  `NEXT_PUBLIC_WORLDS=off` so the CSS worlds are deterministic).
- **Alerting and a restore drill:** `docs/deploy.md` already lists what to alert on (`/health`,
  `ERROR juju.` logs); add Fly Postgres backups and how to restore one, and do it once.

**3. M5 that needs the owner:** the live-stats licence (ESPN is unofficial: keep it, or buy a
feed behind the same interface); legal review of the terms, privacy and responsible-gambling
pages and of state exposure; then a soft launch.

**Later:** choose `LLM_MODEL` with `scripts/eval_lookup.py --model …` once there is a key.

## Needs the owner

1. A **Juju-only Odds API key on the 100K plan** ($59/mo): `JUJU_ODDS_API_KEY` in
   `deploy/laptop/.env` for the preview, a Fly secret for launch. Real captures and historical
   repair need it: historical data is paid-only.
2. **Fly.io:** create the apps, or give a deploy token. Then follow `docs/deploy.md`.
3. **A domain on Cloudflare**, then `SITE_URL` in `web/fly.toml` (share previews).
4. **Turnstile:** a widget for the domain (site key and secret, `docs/deploy.md` step 5).
5. **Optional:** an OpenRouter key and model for the LLM fallback.
6. **Before public launch:** the live-stats licence decision, and legal review.
7. **FYI:** GitHub reports the parlaytracker repository as **public**, while the old goals doc
   said "still private". The owner was told on 2026-09-30.

## Gotchas

- **Local Postgres:** `backend/scripts/dev_postgres.py` (pgserver) prints the URL. The database
  lives in `backend/.pgdata/` and stops if its process is killed; rerun the script to restart
  it. Use `juju_test` for tests (it gets wiped) and `juju_dev` for the seed.
- **Running the stack locally:**
  1. `DATABASE_URL=<juju_dev> python -m juju.cli seed live`
  2. `DATABASE_URL=<juju_dev> uvicorn juju.api.app:app --port 8000`
  3. In `web/`, `npm run build`, then `BACKEND_URL=http://127.0.0.1:8000 npx next start -p 3000`.
     For `e2e/challenge.spec.ts`, build with `NEXT_PUBLIC_TURNSTILE_SITE_KEY=1x00000000000000000000AA`,
     start with `TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA`, and run Playwright
     with `TURNSTILE_E2E=1` (Cloudflare's public test keys, as CI does).
  4. The seeded "live" game goes stale after 5 minutes: cards then show "live status
     unavailable". Reseed before running e2e.
  5. **uvicorn and `next start` don't reload.** After changing backend or web code, restart
     them, or you are testing the old code.
- **Stopping servers:** `pkill -f` can kill your own shell when the pattern also matches the
  command line itself. Stop with `pkill -f "[n]ext-server"` or `pkill -f "[u]vicorn juju"`
  in a command of its own, then start in the next command.
- **This cloud environment's proxy:**
  - pip needs `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt`, and npm needs
    `NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt`.
  - Chromium is at `/opt/pw-browsers`. For WebGL screenshots, launch with `--use-gl=swiftshader`.
    A script importing Playwright must live inside `web/` to resolve `@playwright/test`.
- **Next.js 16:**
  - The font is `Big_Shoulders` in `next/font/google` (not `Big_Shoulders_Display`).
  - Playwright's reduced-motion option goes under `contextOptions`.
  - `e2e/` is type-checked by the Next build.
  - `next/og`'s built-in font has no ✓ ✕ ◷ ☑: `lib/shareImage.tsx` draws those as SVG.
  - `tsconfig.json` allows `.ts` imports so `node --test lib/*.test.ts` runs without a
    bundler; keep the tested `lib/` files (`turnstile`, `signedPass`, `passcode`,
    `unlockPage`) free of `@/` imports, and import each other as `./x.ts`.
  - Middleware is `proxy.ts` now. A redirect from it needs an absolute URL (a relative
    `Location` is a 500), and a rewrite to a page also rewrites that page's link prefetches.
    The passcode gate answers with plain HTML instead.
  - After deleting a page, `npm run typecheck` fails on stale `.next/types` until the next
    `npm run build`.
- **Docker in a cloud container** works (`dockerd` starts), but needs test-only base images:
  Docker Hub rate-limits the shared address, and TLS is intercepted.
  `.claude/skills/deploy-laptop/SKILL.md` has the recipe.
- **Layering the world:** `.world` is `z-index: -1`, and `html.worlds .page` has
  `position: relative; z-index: 1`. Anything else puts the world on top of the text.
- **Migrations:** 0001 was regenerated once, before any deploy; 0002 and 0003 came with M3,
  0004 with the first-touchdown check, 0005 renamed its marker to `plays_checked_at` (written
  by hand: autogenerate turns a rename into a drop and an add, losing the values).
  From now on, add new migrations and never edit an existing one. Generate against a scratch
  database at head (`alembic revision --autogenerate`), read the file, and run
  `tests/db/test_migrations.py`.
- **The licensing guard** in `tests/db/test_api.py` lists every API route. A new endpoint makes
  it fail on purpose: add the route only if it lists prices for one player or team (or is the
  parlay), never an odds board.
- **The "live" seed moves times.** Its prices are real but shown as if captured at T-46. That
  is demo data, which is why `dev_seed.py` reads from `tests/` and the production image doesn't
  include the fixtures. Deciding plays and corrections in `juju_dev` survive a reseed: rebuild
  the schema for a clean slate.
- **ESPN and nflverse clocks differ:** ESPN's play clock is after the play (8:47), nflverse's is
  at the snap (8:56). Don't compare them.
