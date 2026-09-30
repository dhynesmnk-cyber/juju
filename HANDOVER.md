# Handover

For the next agent picking up Juju. **Read [docs/GOALS.md](docs/GOALS.md) first** for the
product, the owner's decisions and the reasons behind them. Then read
[CLAUDE.md](CLAUDE.md) for the commands and the rules that are easy to break. This file says
where the build stands and what to do next. Update it before you finish a session.

## Where things stand (2026-09-30)

| | |
|---|---|
| Branch | `claude/festive-mccarthy-bdwq6b`. Everything is here and nothing is merged to `main`. There is no PR yet: open one only if the owner asks |
| CI | Green on `b8da75c`. It runs three jobs: `backend` (ruff + pytest on Postgres 16), `web` (lint, typecheck, world-size check, build) and `e2e` (seeds the recorded game, runs 11 Playwright tests at phone width) |
| Tests | 1,043 backend tests pass (unit and database), plus 11 Playwright tests |
| Deployed | **No.** The Fly.io config and runbook exist (`docs/deploy.md`); the owner has not created the apps yet |
| Real data | No real capture has run. The archive has only been exercised on recorded fixtures |
| Milestones | M0, M1 and M2 are done, and so is the first part of M3 (worlds and the Jujus). The rest of M3, then M4 and M5, are below |

## What exists

**Backend** (`backend/juju/`):
- `core/`: pure rules, no I/O:
  - `markets.py`: the catalog of 34 Odds API market keys, all verified on a real call;
  - `t45.py`: which archived price a card shows;
  - `card.py`: the outcome state machine and the money;
  - `plays.py`: card ordering;
  - `odds.py`, `settlement.py` and `live.py`, ported from parlaytracker;
  - `models.py`.
- `ingest/`:
  - `odds_api.py`: live, historical and scores endpoints;
  - `espn.py`: box score parsing, plus notable plays for the chips;
  - `router.py`: circuit breakers and host failover, ported;
  - `guards.py`: stale, final and plausibility checks, ported;
  - `resolve.py`: name matching;
  - `parse.py`: the deterministic free-text reader;
  - `llm.py`: the fallback, which stays off until `LLM_API_KEY` and `LLM_MODEL` are set.
- `worker/`: the only process that calls providers:
  - `capture.py`: T-45 capture, historical repair, and bisecting to find a rejected market key;
  - `schedule.py`: games and rosters;
  - `live.py`: the live loop;
  - `archive.py`: writing snapshots and matching player names;
  - `jobs.py`: heartbeat and `check_captures`;
  - `__main__.py`: the scheduler, holding an advisory lock.
- `api/`: FastAPI, private and reached only through the site:
  - `app.py`: the endpoints;
  - `views.py`: builds cards from the archive;
  - `lookup.py`: which player or team a lookup means.
- `cli.py`:
  - `backfill SINCE UNTIL` costs credits and asks first;
  - `unmapped` lists Odds API names not matched to a roster;
  - `seed live|final` loads the recorded game, for development and tests only (`dev_seed.py`).

**Web** (`web/`, Next.js 16):
- Pages: `/` (search, typeahead, Live-now chips), `/g/[game]/[player]`, `/g/[game]/team/[team]`,
  and the info pages.
- `app/api/[...path]/route.ts` is the **only** way into the backend. It forwards an allowlist
  of paths, which keeps Juju inside the licensing terms.
- `components/world/` holds the chosen design (option A):
  - `shader.ts`: a single WebGL shader;
  - `WorldStage.tsx`: starts when idle, lowers quality or falls back to CSS, pauses when hidden;
  - `Jujus.tsx`: the three SVG characters;
  - `worlds.ts`: outcome → world → mood.
- `components/CountUp.tsx`: the server renders the final number; the count runs in the browser
  only.

**Docs**: `docs/GOALS.md` (v2), `docs/licensing.md` (The Odds API clauses we rely on),
`docs/deploy.md` (the Fly and Cloudflare runbook, including the worlds kill switch), and
`docs/GOALS-v1-brainstorm.md` (the owner's original brainstorm, kept unchanged).

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

## Fixtures (reuse them: the Odds API ones cost credits)

| File | What it is |
|---|---|
| `odds_api/nfl_event_odds_2026-09-28_PHI-CHI.json` | Real odds about 2 h before kickoff, 14 markets, 9 `us` books with no Hard Rock. From parlaytracker |
| `odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json` | **Recorded 2026-09-30 for 29 credits**: all 34 keys and all 10 books. 29 markets came back, including Hard Rock, "Yes"-only TD markets, team defenses and "No Touchdown" |
| `espn/nfl_summary_401872963_full.json` | The full PHI @ CHI summary (box score, drives, scoring plays), used by the recent-plays parser and the seed |
| `espn/nfl_roster_{21,3,23,5}.json`, `espn/nfl_scoreboard_2026-09-28_final.json`, `espn/nfl_scoreboard_2026-10-01_scheduled.json` | Rosters and scoreboards for both recorded games. ESPN is free |

The Odds API key in this cloud environment (`ODDS_API_KEY`) is **parlaytracker's free-tier
key**. It had **408 credits** left on 2026-09-30. Don't spend them without the owner's OK.
parlaytracker's own handover says the key should be rotated.

## Next steps

**M3, the rest** (see the plan in GOALS §10):
1. **nflverse verification.**
   - Port `parlaytracker/ingest/nflverse.py` and the `VerifyNfl` logic from
     `parlaytracker/worker/settle.py`. The parlaytracker checkout is at
     `/home/user/dhynesmnk-cyber/parlaytracker`; clone it read-only if it's missing.
   - Add a next-day job, a verified flag, and "Verified" on settled cards.
   - A disagreement sets `games.corrected_at`, which cards already display.
2. **The play that decided it.**
   - Live: `write_box` in `worker/live.py` notes when a stat crosses an archived line, and the
     latest `Play` in that window is shown as "on or around".
   - Next day: nflverse play-by-play gives the exact play.
3. **Lookup golden set.** `backend/tests/fixtures/lookups/golden.jsonl` (150 or more lines) and
   `scripts/eval_lookup.py`, to choose `LLM_MODEL` once there's a key.
4. **Share images.** `opengraph-image.tsx` with `next/og`, showing the card in its world, plus
   a share button.
5. **Production abuse caps.** Turnstile on lookups after the Cloudflare threshold.

**M4, same-game parlays.**
- `core/parlay.py`: legs are `Bet`s, combined with `parlay_decimal`, using the same book when
  possible. A pushed or voided leg drops out. Every card carries the GOALS §5 label.
- `GET /api/parlay/{game}?legs=…`, at most 6 legs, added to the proxy allowlist.
- Multi-leg parsing.
- A parlay tray in the UI. Each leg is a light in the sky, and the gold shockwave fires only when
  all are lit.

**M5, launch hardening.**
- Decide on the live-stats licence (ESPN is unofficial).
- k6 load test.
- Lighthouse CI.
- Visual snapshots per world.
- Alerting and a restore drill.
- Legal review of the terms, privacy and responsible-gambling pages, and of state exposure.
- Soft launch.

## Needs the owner

1. A **Juju-only Odds API key on the 100K plan** ($59/mo), set as a Fly secret. Real captures
   and historical repair need it: historical data is paid-only.
2. **Fly.io:** create the apps, or give a deploy token. Then follow `docs/deploy.md`.
3. **A domain on Cloudflare.**
4. **Optional:** an OpenRouter key and model for the LLM fallback.
5. **Before public launch:** the live-stats licence decision, and legal review.
6. **FYI:** GitHub reports the parlaytracker repository as **public**, while the old goals doc
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
- **Stopping the web server:** `pkill -f "next start"` can kill your own shell, because the
  pattern matches the command itself. Use `pkill -f "[n]ext-server"`, in a command that
  doesn't also contain `next start`.
- **This cloud environment's proxy:**
  - pip needs `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt`, and npm needs
    `NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt`.
  - Headless Chromium fetching external CDNs needs `--ignore-certificate-errors`.
  - Chromium is at `/opt/pw-browsers`. For WebGL screenshots, launch with `--use-gl=swiftshader`.
    Frame rates there are software-rendered and not representative of phones.
- **Next.js 16:**
  - The font is `Big_Shoulders` in `next/font/google` (not `Big_Shoulders_Display`).
  - Playwright's reduced-motion option goes under `contextOptions`.
  - `e2e/` is type-checked by the Next build.
- **Layering the world:** `.world` is `z-index: -1` inside `.page`, which has
  `position: relative; z-index: 1`. Anything else puts the world on top of the text.
- **Migration `0001` was regenerated once, before any deploy.** From now on, add new
  migrations and never edit `0001`.
- **The "live" seed moves times.** Its prices are real but shown as if captured at T-46. That
  is demo data, which is why `dev_seed.py` reads from `tests/` and the production image doesn't
  include it.
