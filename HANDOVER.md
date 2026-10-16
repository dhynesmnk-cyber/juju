# Handover

For the next agent picking up Juju. **Read [docs/GOALS.md](docs/GOALS.md) first** for the
product, the owner's decisions and the reasons behind them. Then read
[CLAUDE.md](CLAUDE.md) for the commands and the rules that are easy to break. This file says
where the build stands and what to do next. Update it before you finish a session.

## Where things stand (2026-10-15)

| | |
|---|---|
| Branch | Everything is in `main`. PR #1 merged M0–M2, PR #2 M3 and M4, PR #3 the parlay leg prices, PR #4 fixes to this file, PR #5 the first-TD scorer check, PR #6 the laptop preview and its passcode gate, PR #7 the late play-by-play retry, PR #8 the CI time limits, PR #9 Phase 1 of the tracker (below), and PR #10 its Phase 2. PR #3 recovered two commits that were pushed to `claude/happy-galileo-p7ify3` after PR #2 merged (`00befec`, cherry-picked, and what was right in `a133cd3`). No PR is open |
| CI | Green on every merged PR head. Three jobs: `backend` (ruff + pytest on Postgres 16), `web` (lint, typecheck, `npm test`, world-size check, build) and `e2e` (seeds the recorded game, 17 Playwright tests at phone width, with Cloudflare's public Turnstile test keys). Each has a time limit of about five times its slowest green run (15, 10 and 20 minutes), so a hung runner fails in minutes, not after GitHub's six hours: on 2026-10-08 an e2e job hung for 18 minutes in `apt-get` (`playwright install --with-deps`), and one re-run passed |
| Tests | 1,913 backend tests (1,164 before the tracker), 9 web unit tests (`node --test`), 17 Playwright tests |
| Deployed | **A private preview, run by the owner** on their own Ubuntu laptop (`deploy/laptop/`, the `deploy-laptop` skill): Docker Compose, Tailscale Funnel, one shared passcode, the recorded demo game. It was deployed end to end in a cloud container on 2026-10-07. It runs: the owner used it through real games on 2026-10-11, with a free Odds API key in `deploy/laptop/.env`. Fly.io (`docs/deploy.md`) is still the launch plan; no Fly apps exist |
| Real data | The laptop captures real prices: on the free key from about 2026-10-10 (until its 500 credits ran out), and on the owner's 20K-credit key from 2026-10-15, which also filled last weekend's missed games from the vendor's history. `python -m juju.cli report --since DATE` on the laptop shows what it saved. The tests still use only the recorded fixtures. nflverse (free) was read for real, to record its fixtures |
| Milestones | M0–M4 are done. M5 (launch hardening) is next: part of it is engineering you can do now, part needs the owner |
| The live test | **On Sunday 2026-10-11 the owner ran the laptop preview through real games with a free Odds API key** (500 credits a month). Lookups of "what just happened" failed, many cards had no price, and the UI felt janky. The causes and the fix, in three stages, are in [docs/plans/live-test-fixes.md](docs/plans/live-test-fixes.md): the backup score polling spent about a credit a minute, every capture asked for all 34 markets, a name-matching bug left prices with no player, a lookup needs a name, and one bad box score can shut an ESPN host for 30 minutes. **On 2026-10-15 the owner put a 20K-credit key on the laptop** (with the old code, so TNF that night was captured) **and asked for all of it to be built. Stage 1 (prices) is done** on `claude/dazzling-newton-n3hzs2`: two captures a game, score polling cut by 75%, the credit budget, re-matched names, no-price reasons, `/api/status` prices, `odds-status` and `report`. **Stage 2 (lookups and live plays) is done** on the same branch: the latest plays for text naming no one, recent plays deciding between namesakes, the tapped play named on its page, interceptions and return TDs as plays, one bad box score no longer shutting an ESPN host. **Stage 3:** the three mockup directions are on a private canvas for the owner (https://claude.ai/artifact/BvBJUwJFtcd6K5xmiJ6hf8: A feed first, B game first, C answer first, phone and desktop, every number from the recorded game), and the fixes every direction needs are built: times the same on server and phone, loading and error pages, no layout shift (floating typeahead, server-rendered banner and 21+ gate), slimmer cards with Details, unpriced bets as one list, 44 px targets, two cards a row on wide screens. **On 2026-10-15 the owner chose B, game first, and it is built:** the home page shows each game on now as a tile (score, its latest play, "Every play, and what it paid"), then the next and earlier games; a game's page (`/g/[game]`, from `/api/game/{id}`, which lists no prices) has its plays on a timeline by quarter, newest first, and tapping one opens what it paid in place (beside the timeline on a wide screen), plus Players and Game lines tabs. Result pages link back to their game |
| The tracker | The owner's own bet tracker, parlaytracker, is being folded into Juju as a private `/me` section ("Watch my parlays"), in six phases: [docs/plans/integrate-parlaytracker.md](docs/plans/integrate-parlaytracker.md). **Phases 1 (the backend core) and 2 (its worker jobs) are done**; Phase 3 (passcode users and `/api/me`) is next. Nothing user-visible yet: no routes and no web change. The worker runs the tracker's jobs, which do nothing until a slip is logged, apart from the canary (at startup and 09:00 ET: the latest NFL box score from each ESPN host, and nflverse) |

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
- `tracker/` (Phases 1 and 2 of the plan above): parlaytracker@c3bd43c's core and worker
  jobs, ported with `Ported from` headers. No route calls it yet:
  - `models.py`: slips, legs, events (any of four sports), tags, sportsbooks, raw samples, on
    Juju's `Base` (migration 0006). Leg sources are `LegSource`, PT's `DataSource`: Juju's has
    `odds_api` and no `manual`;
  - `services.py`: the **only** write path for slips and legs (services flush, callers commit);
  - `settlement.py`, `live.py` (the Watch read model), `analytics.py`, `closing.py`,
    `markets.py`, `schemas.py`: PT's rules. Where Juju has the same rule, the tracker uses
    Juju's (odds, `settle_over`/`settle_spread`, freshness and status text, name matching);
  - `resolve.py` (the slip resolver), `slip_import.py` (the CSV importer), `fetch.py` (ESPN
    scoreboards and rosters for any sport, through Juju's router);
  - `feed.py`: ESPN and The Odds API as the tracker's jobs ask for them, over Juju's router and
    client (`EspnFeed`, `OddsFeed`), and its box-score columns: its NFL markets read exactly
    Juju's `Stat` columns, plus NBA points and NHL goals + assists;
  - `nflverse.py`: a market's value and offensive snaps, on Juju's streaming client;
  - `worker/`: `settle.py` (finals, settlement, recheck, nflverse verification, the canary,
    pruning, the raw-sample sink), `live.py` (the live poll for pending legs), `closing.py`
    (closing lines, only with a key) and `jobs()`, which `juju/worker/__main__.py` registers as
    `tracker_*` jobs on the same router and client.
- `cli.py`: `backfill`, `unmapped`, `verify` (runs the nflverse check now), `seed live|final`,
  and the tracker's `import-slips CSV --user NAME [--apply]` (a dry run without `--apply`;
  `-` reads standard input), `check-import CSV`, `tracker-backfill` (takes the worker's lock),
  `export-sample ID PATH` and `export-recording EVENT DIR` (games in `RECORD_EVENT_IDS`).
- `scripts/`: `dev_postgres.py`; `record_nflverse.py` (slices nflverse files into fixtures,
  free; `--tracker` records the tracker's slice, snap counts included); `eval_lookup.py` (the
  golden set with or without an LLM, to choose `LLM_MODEL`).

**Web** (`web/`, Next.js 16):
- Pages: `/` (search, then a tile per game on now, then next and earlier games), `/g/[game]`
  (the game: its plays by quarter, each opening what it paid in place; Players; Game lines),
  `/g/[game]/[player]`, `/g/[game]/team/[team]`, `/g/[game]/parlay?legs=` (M4) and the info
  pages. Result pages take `?card=` to put a shared card first, and `?play_id=` to name the
  play they were opened from.
- `app/api/[...path]/route.ts` is the **only** way into the backend: an allowlist of paths.
  It also verifies Turnstile tokens (`lib/turnstile.ts`) and sets a signed one-hour cookie.
- `.../image/route.tsx` under both result pages (M3): the share image, one card in its world
  with the Jujus (`lib/shareImage.tsx`, next/og). Pages emit Open Graph tags for it.
- `components/`: `LiveNow` (the home page's game tiles), `GamePage` (the timeline; a play's
  answer is the player's or team's own endpoint with `play_id`, its priced touched bets, two
  at most), `ResultCard` (Verified chip, deciding play, Share and Add to parlay),
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

**Docs**: `docs/plans/integrate-parlaytracker.md` (the tracker: the owner's plan, corrected
after Phase 1, with checklists of what only PT's Streamlit pages held), `docs/GOALS.md` (v2:
decisions, milestones), `docs/licensing.md`, `docs/deploy.md`
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
11. **parlaytracker is folded into Juju** (2026-10-08) as a private `/me` section, "Watch my
    parlays" (`docs/plans/integrate-parlaytracker.md` §2). All six Streamlit pages are rebuilt
    in Next.js, and its Python logic moves into the backend. Per-user passcodes extend the
    passcode gate. Fresh data: nothing is migrated, but the CSV importer is kept. At the end the
    parlaytracker repo is archived. The Fly launch plan is unchanged; `/me` stays private.
12. **After the live test** (2026-10-12, `docs/plans/live-test-fixes.md`):
    - **Prices:** build everything and test it on the free key first; a paid plan comes
      later. So the capture rations credits on a small quota (one capture per game, the
      most-asked bets first) and captures everything on a large one.
    - **A lookup that names nobody** ("what just happened", "that TD") offers the latest
      plays to tap, newest first, and never picks one by itself.
    - **The UI gets a bigger redesign**, with mockups for the owner to choose from before
      it is built. **On 2026-10-15 the owner chose B, game first**, from
      https://claude.ai/artifact/BvBJUwJFtcd6K5xmiJ6hf8: games on the home page, and a game
      page whose timeline of plays opens what each paid.
    - **On 2026-10-15:** a **20K-credit Odds API key** (put on the laptop that evening);
      **captures at T-3h and T-45 only** (was T-3h, T-60, T-49, T-47 and T-45:30); **75% less
      credit use during games** (score polling every 4 minutes, not every minute); last
      weekend's missed games filled from the vendor's history once; **build it all now**. The
      key is never sent in chat.

## Decisions made by agents (reasons in the commits; change only with a reason)

- **Lookups that name no one get the latest plays** (2026-10-15, the owner's choice): up to
  8, newest first by ESPN's wallclock, from games in play or final within the hour, filtered by
  the kind of play and the yards (±5) described. A touchdown pass is offered twice: as the
  scorer's and as the thrower's ("J. Hurts 30-yd TD pass"). No prices in them, so
  `/api/lookup` stays a UI list (licensing).
- **A namesake who just made the play is the one meant:** among candidates with a strong name
  match (90+), one with a play of the kind described in the last ten minutes is picked (path
  `recent`) if he is the only one, and listed first otherwise. A first name alone is still
  never picked. This refines GOALS §7.3 (the confirmation card when unsure): the play itself
  removes the doubt.
- **Games final within the hour are searched with the live ones**, so "Smith TD" about a game
  that just ended is no longer resolved to a lone Smith in a game still on.
- **One game's bad box score benches that game on that host for 5 minutes**
  (`ingest/router.py`), instead of closing the host for 30 minutes for every game. Two
  different games failing within 10 minutes is a format change and closes it as before. The
  fault-matrix test was changed to match.
- **Interceptions are notable plays** (the defender's, with the thrower kept as the passer, so
  "that pick" finds both), labelled "T. Edwards interception"; return touchdowns are labelled
  from ESPN's play type ("pick-six", "punt return TD"); a play stored before its team's roster
  arrived gets its player on a later read. Migration 0008 adds `plays.passer_espn_id`.
- **The T-45 capture starts at T-46** (2026-10-15), so a failed call has a minute of 20-second
  retries before T-45:00. With one on-time capture instead of three, a 30-second window was too
  thin. Its price is still on time (within 5 minutes of T-45).
- **The credit budget shares what is left over the games left** before the reset, counting at
  least a full week's games for each week left (`GAMES_PER_WEEK`), because the vendor lists
  only a few days ahead. An unknown quota is a full plan. The T-45 capture may still spend the
  reserve; the T-3h check, repair and score polling may not.
- **"Prices are off" is read from the Odds API's last success** (none for 2 hours), not from
  a flag the worker writes: with a key, the free games list succeeds every 30 minutes. The API
  never holds the key.
- **A market missing from a game's snapshots says why:** not asked for (`NOT_SAVED`; "credits
  were short" only when the game's plan was lean, since the demo recording simply lacks the
  moneyline), or asked for and not offered (`NOT_OFFERED`). `NO_SNAPSHOT` is only for a game
  with no snapshot at all, and uses the worker's note when it skipped the game.
- **Unmatched and doubtful player names are matched again** with every snapshot and after every
  roster refresh, for games of the last 14 days (`archive.remap_prices`). The prices keep the
  vendor's raw name, so pointing them at a player changes no number.

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
- **The tracker shares Juju's code only where the rule is identical** (checked line by line in
  Phase 1). Otherwise it keeps parlaytracker's version in `tracker/`: Juju's cards and lookups
  depend on Juju's. Its tables keep parlaytracker's names (`events`, `slips`, …), beside
  Juju's `games`.
- **The migration test now compares catalogs** (columns, constraints including CHECK text,
  indexes) between the migrated schema and `create_all`, as parlaytracker's did: Alembic's
  comparison doesn't see CHECKs, and the tracker's integrity lives in them.
- **`check-import` no longer crashes** on a disagreeing leg with no final value (a void, or a
  result entered by hand). That was a parlaytracker bug, found by a new test.
- **One box-score parser for both** (Phase 2): Juju's takes a column table, and the tracker's
  NFL markets are Juju's `Stat` columns. Checked identical to parlaytracker's parser on every
  recorded box score. Never add a member to `Stat` for the tracker.
- **The tracker's closing lines ask for Juju's book chain** (Hard Rock first), not
  parlaytracker's `regions=us`, which never returned Hard Rock. Same cost, one quota; the
  capture stops at the reserve.
- **The raw-sample sink is on the shared router**, so Juju's own parse failures are kept too
  (the last 5 per provider, pruned daily): failures are visible.

## Fixtures (reuse them: the Odds API ones cost credits)

| File | What it is |
|---|---|
| `odds_api/nfl_event_odds_2026-09-28_PHI-CHI.json` | Real odds about 2 h before kickoff, 14 markets, 9 `us` books with no Hard Rock (5 of them offshore). From parlaytracker |
| `odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json` | **Recorded 2026-09-30 for 29 credits.** It asked for all 34 keys and all 10 books; 29 markets came back, from 8 books (no Caesars or Fanatics, which need a paid plan) |
| `espn/nfl_summary_401872963_full.json` | The full PHI @ CHI summary, used by the plays parser and the seed |
| `espn/nfl_roster_{21,3,23,5}.json`, `espn/nfl_scoreboard_*.json` | Rosters and scoreboards for both recorded games (`nfl_roster_22.json`, Arizona, is only for a parser test). ESPN is free |
| `nflverse/*.csv` | **Recorded 2026-09-30, free**: PHI @ CHI (final 27–7) and PIT @ CLE (not played) from nflverse's schedule, its 65 stat lines and players, and its 155 plays. Re-record with `scripts/record_nflverse.py` |
| `espn/{nba,nhl}_scoreboard_2026-03-01.json`, `espn/mlb_scoreboard_2026-05-05_postponed.json`, `espn/{nba_roster_18,nhl_roster_16,mlb_roster_10}.json`, `espn/nba_summary_*.json`, `espn/nhl_summary_*.json` | From parlaytracker (free): the other sports' scoreboards, rosters and box scores, for the tracker's slips. Its ten files that share a path with Juju's are byte-identical |
| `nflverse/tracker/*.csv` | **Recorded 2026-10-08, free** (`scripts/record_nflverse.py --tracker 2026 401872954 401872958`): NYJ @ DET and ARI @ SF (week 3) with snap counts, for the tracker's worker tests. parlaytracker's own slice had 0 in 21 values of its four newest markets; these are nflverse's real numbers |
| `lookups/golden.jsonl` | 189 lookups about the recorded game with their right answers (`tests/lookups.py`): 138 exact, 50 asked, 1 missed, 0 wrong without the LLM (2026-10-15; text naming no one counts as exact when it gets the latest plays). `tests/db/test_slate.py` adds a second game (PIT @ CLE, rosters only) for namesakes, recent plays and games just over |

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

**0. The live-test fixes** ([docs/plans/live-test-fixes.md](docs/plans/live-test-fixes.md)):
All three stages are done: prices, lookups and live plays, and the UI (direction B, game
first). Next: the owner pulls the branch on the laptop before Sunday 2026-10-18 and tries the
game pages during the games. After Sunday, ask the owner for
`report --since 2026-10-18` from the laptop: it shows whether every game got its two captures
and how many credits each cost. These come before the tracker's Phase 3.

**0b. The tracker, Phase 3: passcode users and `/api/me`** (`docs/plans/integrate-parlaytracker.md`
§7 and §10). Read that phase's checklist of what only parlaytracker's Streamlit pages held. It
needs the owner's two usernames before deploy, not before the code. Then Phases 4–6 in order.
The owner may interleave M5 (below).

**1. Follow-ups from M3 and M4** (small, each a commit):
- **A share image for a parlay:** `/g/[game]/parlay/image?legs=`, reusing `lib/shareImage.tsx`
  (the lights in the sky, the combined payout, the correlation note), and Open Graph tags on
  the parlay page.
- **The golden set:** add parlay lines (they resolve to `kind: "parlay"`; `tests/lookups.py`
  needs a verdict for them). Nicknames were done on 2026-10-15; "game total over" is the one
  miss left. Raise the floors in `tests/db/test_golden.py` when the numbers improve. A real
  Sunday's lookups (`report --since`) are the best source of new lines.

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

1. **Done 2026-10-15:** a Juju-only Odds API key, on a 20K-credit plan, in
   `deploy/laptop/.env` as `JUJU_ODDS_API_KEY`. For launch it becomes a Fly secret. If its
   credits don't reset on the 1st, the owner sets `ODDS_API_RESET_DAY` in `.env`.
2. **Fly.io:** create the apps, or give a deploy token. Then follow `docs/deploy.md`.
3. **A domain on Cloudflare**, then `SITE_URL` in `web/fly.toml` (share previews).
4. **Turnstile:** a widget for the domain (site key and secret, `docs/deploy.md` step 5).
5. **Optional:** an OpenRouter key and model for the LLM fallback.
6. **Before public launch:** the live-stats licence decision, and legal review.
7. **FYI:** GitHub reports the parlaytracker repository as **public**, while the old goals doc
   said "still private". The owner was told on 2026-09-30.
8. **For the tracker** (`docs/plans/integrate-parlaytracker.md` §13): the two users are **Chris
   and Ash** (the owner, 2026-10-08). Their passcodes are theirs to put in `deploy/laptop/.env`
   once Phase 3 exists: never in the repo or in chat, and long enough for a page on the public
   internet (the owner was told on 2026-10-08 that four digits are not); optionally an OpenRouter key for the screenshot reader
   (`QWEN_API_KEY`, Phase 4g); a backup destination and one restore drill (Phase 6); a real
   game weekend for acceptance; approval to archive the parlaytracker repo at the end; and
   whether the laptop gets parlaytracker's auto-update and backup timers. The 25 historical
   Hard Rock slips are on the owner's side as a CSV: never in the repo.

## Gotchas

- **`up.sh` runs under `pipefail`:** never end a pipe there with `grep -q`. It stops reading at
  the first match, the writer fails on the closed pipe (curl exits 23), and the check fails.
  Pages stream since the loading pages arrived, so on 2026-10-16 the demo-page check failed
  every time on the laptop. Use `grep … > /dev/null`, or capture the output first.
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
- **The 21+ gate is a cookie** (`juju21=yes`, a year), read by the server in `app/layout.tsx`
  (which makes every page dynamic), with the old localStorage key still honoured and carried
  into the cookie. Playwright's `pastAgeGate` sets both.
- **Times:** `lib/format.ts` formats in ET unless given the viewer's zone; `LocalTime` adds the
  local time after hydration. Never format a time in the runtime's own zone in a component: the
  server is on UTC and the page would redraw. `e2e/juju.spec.ts` runs a page in Los Angeles
  time and fails on React's hydration errors (#418 and friends in a production build).
- **Pages:** `lib/backend.ts` `backendPage` turns a 404/422 into `notFound()` and anything else
  into `app/error.tsx` ("can't reach its data"), with an 8-second timeout. `backendGet` (6 s)
  is for things a page can do without.
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
  by hand: autogenerate turns a rename into a drop and an add, losing the values), 0006 is the
  tracker's schema, 0007 adds `games.capture_plan` and `capture_note` (the credit budget), 0008
  `plays.passer_espn_id`.
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
- **The tracker's tests** live in `tests/unit/tracker/` and `tests/db/tracker/`, with helpers in
  `tests/tracker_support.py`. Its database tests run in a rolled-back savepoint (`session`), and
  an autouse fixture puts back migration 0006's four sportsbooks, because Juju's `db` fixture
  empties every table after its tests. The worker's tests stage nflverse cases by editing rows
  (`tracker_support.nflverse_loader(patch)`). A "SPEC.md" in a tracker docstring is
  parlaytracker's.
  To read parlaytracker itself: `git clone --depth 1
  https://github.com/dhynesmnk-cyber/parlaytracker` (public; read only, never change it).
- **ESPN and nflverse clocks differ:** ESPN's play clock is after the play (8:47), nflverse's is
  at the snap (8:56). Don't compare them.
