# Handover

For the next agent picking up Juju. **Read [docs/GOALS.md](docs/GOALS.md) first** for the
product, the owner's decisions and the reasons behind them. Then read
[CLAUDE.md](CLAUDE.md) for the commands and the rules that are easy to break. This file says
where the build stands and what to do next. Update it before you finish a session.

## Where things stand (2026-09-30, second session)

| | |
|---|---|
| Branch | `claude/happy-galileo-p7ify3`. PR #1 merged M0–M2 into `main`; everything since (M3) is on this branch only. There is no open PR: open one only if the owner asks |
| CI | Green through the share-image commit (`d7712a8`). Three jobs: `backend` (ruff + pytest on Postgres 16), `web` (lint, typecheck, `npm test`, world-size check, build) and `e2e` (seeds the recorded game, 14 Playwright tests at phone width, with Cloudflare's public Turnstile test keys) |
| Tests | 1,105 backend tests, 3 web unit tests (`node --test`), 14 Playwright tests |
| Deployed | **No.** The Fly.io config and runbook exist (`docs/deploy.md`); the owner has not created the apps yet |
| Real data | No real capture has run. The archive has only been exercised on recorded fixtures. nflverse (free) was read for real, to record its fixtures |
| Milestones | M0–M3 are done. M4 (same-game parlays) is next, then M5 |

## What exists

**Backend** (`backend/juju/`):
- `core/`: pure rules, no I/O:
  - `markets.py`: the catalog of 34 Odds API market keys, all verified on a real call;
  - `t45.py`: which archived price a card shows;
  - `card.py`: the outcome state machine and the money;
  - `plays.py`: card ordering;
  - `odds.py`, `settlement.py` and `live.py`, ported from parlaytracker;
  - `models.py`, including `StatCorrection` and `DecidingPlay` (migrations 0002, 0003).
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
  - `capture.py`, `schedule.py`, `archive.py`, `jobs.py`: as before;
  - `live.py`: the live loop. `write_box` records corrections after settling and never
    overwrites a value nflverse supplied; each read also notes line crossings;
  - `verify.py` (M3): `VerifyGames`, 10:07 and 16:07 ET and at startup. It checks last week's
    final games against nflverse: agreeing stats are marked verified, a disagreeing one is
    replaced by nflverse's and recorded in `stat_corrections`, a disputed final score applies
    nothing (`games.last_error`). Then it finds the exact deciding plays;
  - `deciding.py` (M3): the play that decided a bet. Live: "on or around", the latest notable
    play by that player among the plays in the read that saw the stat pass the line (only the
    game clock if there is none, or on a game's first read). Next day: exact, from the
    play-by-play, kept only if the play-by-play adds up to the final stat;
  - `__main__.py`: the scheduler, holding an advisory lock.
- `api/`: FastAPI, private and reached only through the site:
  - `app.py`: the endpoints. Lookups: 30 a minute per client, and after 12 in 15 minutes a
    428 Turnstile challenge when the site has Turnstile on;
  - `views.py`: cards, now with `verified`, `decided_by` and specific correction notes;
  - `lookup.py`: which player or team a lookup means; reads the play from the named player's
    side (a defender's interception is his).
- `cli.py`: `backfill`, `unmapped`, `verify` (runs the nflverse check now), `seed live|final`.
- `scripts/`: `dev_postgres.py`; `record_nflverse.py` (slices nflverse files into fixtures,
  free); `eval_lookup.py` (the golden set with or without an LLM, to choose `LLM_MODEL`).

**Web** (`web/`, Next.js 16):
- Pages: `/`, `/g/[game]/[player]`, `/g/[game]/team/[team]` and the info pages. Result pages
  take `?card=` to put a shared card first.
- `app/api/[...path]/route.ts` is the **only** way into the backend: an allowlist of paths.
  It also verifies Turnstile tokens (`lib/turnstile.ts`) and sets a signed one-hour cookie.
- `.../image/route.tsx` under both result pages (M3): the share image, one card in its world
  with the Jujus (`lib/shareImage.tsx`, next/og). Pages emit Open Graph tags for it.
- `components/`: `ResultCard` (Verified chip, deciding play, Share button), `ResultView`,
  `SearchBox` (shows `TurnstileWidget` only when challenged), `world/` (option A: shader, Jujus).

**Docs**: `docs/GOALS.md` (v2, milestones updated), `docs/licensing.md`, `docs/deploy.md`
(Fly, Cloudflare including Turnstile and the share-image cache, the worlds kill switch, the
verification job), `docs/GOALS-v1-brainstorm.md` (unchanged).

## Decisions the owner made (don't reopen them)

1. **Payouts:** $10 at T-45, actual result first, fair value alongside.
2. **Prices:** self-archived real prices. Never invent or interpolate one.
3. **Input:** free text, player typeahead and tappable plays. The confirmation card appears only
   when the match is unsure.
4. **Scope:** player props, game lines and same-game parlays (parlays are M4).
5. **Book order:** Hard Rock Bet, then DraftKings, then the others (`BOOKS` setting).
6. **Hosting:** Fly.io in `iad`, with Cloudflare in front.
7. **Odds vendor:** The Odds API. Its terms allow a user-facing commercial app but not
   redistributing the raw data. So no endpoint may list or export prices across players or
   games, and the backend has no public address.
8. **Look:** option A, a shader world that follows the bet's state, plus the Jujus. The owner
   chose it from the mockup at https://claude.ai/artifact/E1syoBM4LP14DMPjKiLy73, which is
   private to them.

## Decisions made this session (reasons in the commits)

- **A disagreement with nflverse re-opens the payout** with the official number, and the card
  says what changed. That follows GOALS v1 section 9 ("a settled payout can be re-opened and
  re-labelled if the official stat changes"); parlaytracker sent it to a person instead, and
  Juju has no one to review. A disputed *final score* is never applied.
- **Only stats that matched ESPN exactly on a real game are checked.** A mapping that drifts
  would "correct" good numbers. `tests/unit/test_nflverse.py` pins the agreement.
- **A first name alone is offered, never picked** ("Will", "Chance", "Case" are players here).
- **The edge rule for lookups is a block, not a managed challenge.** A challenge page can't be
  solved from a `fetch`; Turnstile runs in the page instead.

## Fixtures (reuse them: the Odds API ones cost credits)

| File | What it is |
|---|---|
| `odds_api/nfl_event_odds_2026-09-28_PHI-CHI.json` | Real odds about 2 h before kickoff, 14 markets, 9 `us` books with no Hard Rock. From parlaytracker |
| `odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json` | **Recorded 2026-09-30 for 29 credits**: all 34 keys and all 10 books |
| `espn/nfl_summary_401872963_full.json` | The full PHI @ CHI summary, used by the plays parser and the seed |
| `espn/nfl_roster_{21,3,23,5}.json`, `espn/nfl_scoreboard_*.json` | Rosters and scoreboards for both recorded games. ESPN is free |
| `nflverse/*.csv` | **Recorded 2026-09-30, free**: PHI @ CHI (final 27–7) and PIT @ CLE (not played) from nflverse's schedule, its 65 stat lines and players, and its 155 plays. Re-record with `scripts/record_nflverse.py` |
| `lookups/golden.jsonl` | 177 lookups about the recorded game with their right answers (`tests/lookups.py`) |

The Odds API key in this cloud environment (`ODDS_API_KEY`) is **parlaytracker's free-tier
key**. It had **408 credits** left on 2026-09-30 and none were spent this session. Don't spend
them without the owner's OK. parlaytracker's own handover says the key should be rotated.

## Next steps

**M4, same-game parlays.**
- `core/parlay.py`: legs are `Bet`s, combined with `parlay_decimal`, using the same book when
  possible. A pushed or voided leg drops out. Every card carries the GOALS §5 label.
- `GET /api/parlay/{game}?legs=…`, at most 6 legs, added to the proxy allowlist.
- Multi-leg parsing.
- A parlay tray in the UI. Each leg is a light in the sky, and the gold shockwave fires only when
  all are lit.

**Smaller follow-ups found this session.**
- Check the first-TD scorer and the longest rush and reception against the play-by-play (the
  data is already loaded by `VerifyGames`; they are shown as "can't be verified" today).
- Exact deciding plays are found only in the run that verifies a game; a game whose
  play-by-play was late never gets them. A later run could fill them in.
- The golden set's 3 misses are team nicknames ("Philly", "Birds") and "game total over".
- Choose `LLM_MODEL` with `scripts/eval_lookup.py --model …` once there is a key.

**M5, launch hardening.**
- Decide on the live-stats licence (ESPN is unofficial).
- k6 load test; Lighthouse CI; visual snapshots per world (and per share image).
- Alerting and a restore drill.
- Legal review of the terms, privacy and responsible-gambling pages, and of state exposure.
- Soft launch.

## Needs the owner

1. A **Juju-only Odds API key on the 100K plan** ($59/mo), set as a Fly secret. Real captures
   and historical repair need it: historical data is paid-only.
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
  2. `uvicorn juju.api.app:app --port 8000`
  3. In `web/`, `npm run build`, then `BACKEND_URL=http://127.0.0.1:8000 npx next start -p 3000`.
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
    bundler; keep `lib/turnstile.ts` free of `@/` imports.
- **Layering the world:** `.world` is `z-index: -1` inside `.page`, which has
  `position: relative; z-index: 1`. Anything else puts the world on top of the text.
- **Migrations:** 0001 was regenerated once, before any deploy; 0002 and 0003 were added this
  session. From now on, add new migrations and never edit an existing one.
- **The "live" seed moves times.** Its prices are real but shown as if captured at T-46. That
  is demo data, which is why `dev_seed.py` reads from `tests/` and the production image doesn't
  include it. Deciding plays and corrections in `juju_dev` survive a reseed: rebuild the
  schema for a clean slate.
- **ESPN and nflverse clocks differ:** ESPN's play clock is after the play (8:47), nflverse's is
  at the snap (8:56). Don't compare them.
