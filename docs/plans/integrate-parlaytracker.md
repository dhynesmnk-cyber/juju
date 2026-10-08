# Plan: fold ParlayTracker into Juju as "Watch my parlays"

**Status:** approved direction (owner, 2026-10-08). **Phases 1 and 2 are done** (see §10);
Phases 3–6 are not started.
**Source:** `parlaytracker@c3bd43c`, its current HEAD and the commit Juju's ported files
already cite.
**Target:** `juju@ef6fa70` and onward.

> **Edited 2026-10-08, after reading both codebases for Phase 1.** The owner's text is kept, with
> these corrections made in place:
>
> - Juju's `ingest/espn.py` already has ParlayTracker's multi-sport scoreboard and roster
>   parsers, unchanged. Only the box score differs: PT keys stats by `MarketType` for four
>   sports, Juju by `Stat` for the NFL. Phase 1 needed no box score, so that port is in Phase 2.
> - Juju's router already had `sample_sink` and `record_event_ids`. It only needed an optional
>   `sport` on `scoreboard()` and `roster()`, which Phase 1 added.
> - Leg sources can't use Juju's `DataSource`: Juju's has `odds_api` and no `manual`, and three
>   Juju tables check it. The tracker has its own `LegSource`, with PT's values.
> - Phase 1 needed no new settings or dependencies. Each setting arrives with the phase that
>   reads it (§6.6).
> - Juju's LLM fallback calls OpenRouter with plain httpx, not the openai SDK, so §6.2's "same
>   pattern" was wrong. Phase 4g decides.
> - Some of PT's logic lives only in its Streamlit pages. The checklists under Phases 3–4 list
>   it, so the rebuild doesn't lose it.
> - Three code blocks were lost when the plan was pasted, and have been reconstructed. Each is
>   marked as such.

## 1. The goal in one sentence

The private two-user bet tracker (ParlayTracker: log real slips, watch them live, settle them,
review the exceptions, see where the edge is) becomes a passcode-gated `/me` section of Juju,
running in Juju's existing stack on the same laptop: one Postgres, one worker, one FastAPI
backend, one Next.js UI. The ParlayTracker repo is then archived.

## 2. Owner decisions (2026-10-08, don't reopen)

| | Decision |
|---|---|
| UI | Rebuild all six Streamlit pages natively in Next.js inside `web/`. No Streamlit container survives. The Python logic (services, analytics, live rules, settlement) ports into the Juju backend; only the Streamlit rendering layer is thrown away. |
| Auth | Per-user passcodes, extending Juju's existing signed-cookie gate. The public site keeps `SITE_PASSCODE`; `/me` needs one of two user passcodes. Works over Funnel from anywhere, with or without Tailscale. |
| Data | Fresh start. No live ParlayTracker database to migrate. The CSV importer is ported, so the 25 historical Hard Rock slips can be re-imported later if the owner wants. |
| End state | Juju absorbs ParlayTracker. All code moves into this repo; the parlaytracker repo gets archived with a README pointer. Juju's public-launch plan (Fly.io) is unchanged; `/me` stays a private section behind its own gate. |

## 3. What exists on each side

| | Juju (`backend/juju/`, `web/`) | ParlayTracker (`parlaytracker/`) |
|---|---|---|
| Product | Public: what $10 at T-45 would have paid | Private: log real bets, watch, settle, analyse |
| UI | Next.js 16, own design system, worlds, share images | Streamlit, 6 pages, Tailscale-login auth |
| API | FastAPI, allowlisted through `web/app/api/[...path]` | none (Streamlit talks to the DB directly) |
| DB | Postgres 16, Alembic 0001–0005 | Postgres 16, Alembic 0001–0003 |
| Worker | APScheduler, advisory lock: schedule/rosters/live poll, T-45 capture, repair, nflverse verify, deciding plays | APScheduler, advisory lock: closing capture, NFL live poll (cadence), finals, settle, recheck, nflverse verify, canary, prune |
| Shared lineage | `core/odds.py`, `settlement.py`, `live.py`, `ingest/{http,espn,guards,router,odds_api,nflverse,resolve}.py` were ported from parlaytracker@c3bd43c and have since diverged in both directions | the originals |
| Tests | 1,164 backend, 9 web unit, 17 Playwright | about 1,849 (unit, db, Streamlit app) |
| Deploy | `deploy/laptop/` compose + `up.sh`, Funnel + shared passcode | `deploy/` compose, Tailscale Serve + `ALLOWED_LOGINS`, backup/restore scripts, systemd timers |

Both already agree on the fundamentals:
- Python 3.12, SQLAlchemy 2, Alembic, pydantic-settings.
- VARCHAR+CHECK enums, with no native Postgres enum types, so no type collisions when the
  schemas merge.
- Identical Alembic naming conventions.
- httpx with circuit breakers.
- `source_health` tables of the same shape.
- Secrets as `SecretStr`, fixture-only tests, and an advisory-locked single worker.

## 4. Target architecture

_Reconstructed: the original diagram was lost when the plan was pasted._

```
phone (either user) ──▶ Tailscale Funnel ──▶ web (Next.js, the only public surface)
                                               │  proxy.ts: SITE_PASSCODE gate, and for /me and
                                               │  /api/me a juju_tracker cookie (one per user)
                                               │
                         /api/* (allowlist)    │    /api/me/* (tracker cookie verified,
                         public, unchanged     │     x-juju-user: <name>, no-store)
                                               ▼
                                     api (FastAPI, no public address)
                                       juju/api/app.py      the public cards
                                       juju/api/me.py       the tracker's read models and writes
                                               │
                                               ▼
                                  Postgres (one database: juju)
                                    Juju's tables: games, prices, live_stats …
                                    the tracker's: slips, legs, events, tags, sportsbooks …
                                    source_health (shared)
                                               ▲
                                               │
                         worker (one process, one advisory lock, one scheduler)
                           Juju's jobs + the tracker's (settle, finals, closing lines …)
                           ──▶ ESPN (one router, one rate limit), nflverse, The Odds API
```

There are no new containers. The Streamlit service and ParlayTracker's compose and database
disappear. Its worker jobs move into Juju's worker, and its pages become Next.js routes.

## 5. UI/UX: "Watch my parlays"

### 5.1 Information architecture

- **Route group `web/app/me/`.** Everything private lives under `/me`.
- **Header.** When the request carries a valid tracker cookie, the top nav grows a "My parlays"
  link with two live badges: the number of slips in progress and the number of open review
  items. Without the cookie the link is not rendered at all, so the public site looks exactly as
  it does today.
- **Sub-nav.** Inside `/me`, a compact pill sub-nav: mobile-first, one row, scrolls. Watch ·
  Log · Reviewⁿ · Stats · Settings.
- **`/me` (index) is Watch,** the headline feature. The owner's name for this whole effort is
  "watch my parlays", so watching pending slips is what you land on.
- **Copy discipline.** The public site's hypothetical T-45 "parlay card" and the tracker's real
  logged slips must never be confusable.
  - `/me` copy says "your logged bets".
  - The tracker footer repeats "Juju is not a sportsbook", plus "This section records bets you
    placed elsewhere. It takes no bets."

### 5.2 Page mapping (Streamlit → Next.js)

| ParlayTracker page | New route | Notes |
|---|---|---|
| Live (`pages/live.py`) | `/me` (Watch) | the flagship; see 5.3 |
| Log (`pages/log.py` + `components/slip_form.py`) | `/me/log` | the 10-second quick-add form; see 5.4 |
| Screenshot (`pages/screenshot.py`) | `/me/log?screenshot=1` (upload panel atop the same form) | one form, two entry points: mirrors PT's design, where the reader only pre-fills the ordinary form |
| Review (`pages/review.py`) | `/me/review` | queue + inline actions |
| Analytics (`pages/analytics.py`) | `/me/stats` | CLV first, low-sample greying, every figure with its n |
| Settings (`pages/settings.py`) | `/me/settings` | tags, sportsbooks, source health, worker heartbeat |
| Health banner (`app/common.py`) | shared component in the `/me` layout | heartbeat stale >3 min, breaker open >5 min, "ESPN changed its format" for schema |

### 5.3 Watch page (`/me`): spec

- **Cards.** One card per pending slip with at least one active game, newest kickoff first;
  then "Settled in the last 7 days".
- **Card chrome.** It reuses Juju's card visual language: ResultCard-style chrome, and the
  freshness colours Juju already has in `lib/types.ts` (ok, amber, red).

_Reconstructed card anatomy (the original block was lost). The wording is PT's
(`app/pages/live.py`, `app/common.py`, `core/live.py`); the values are made up, and team names
are shortened (a leg shows ESPN's full names):_

```
┌─────────────────────────────────────────────────────────────────────────────
│ Same-game parlay +450 · Hard Rock · $40.00 to win · logged by alice         ← slip_summary
│ ARI @ SF · Receptions · Trey McBride o5.5 — 4 · under · Q3 8:42             ← leg_line, per leg
│ ARI @ SF · Touchdowns · George Kittle o0.5 — 1 · over · Q3 8:42
│ ARI @ SF · Alt spread · San Francisco 49ers -3.5 — 7 · covering · Q3 8:42
│ 2 of 3 over                                                                 ← progress_text
│ updated 25 s ago · via espn_web                                             ← green, amber or red
│ No change for 6 min                                                         ← only while the clock stands still
└─────────────────────────────────────────────────────────────────────────────
```

- **Leg wording.**
  - A leg with no value yet reads "no stat line yet" (a player) or "waiting for a score" (a
    team).
  - A non-NFL leg reads "Not tracked live: settles after the game".
  - A settled leg shows its result.
- **The rules are already ported and tested.** All of the state maths is pure and exactly
  tested in the tracker's `live.py` (ported in Phase 1), and serves as the server-side read
  model. That covers:
  - over/under/level and "N to go";
  - the progress text;
  - freshness: amber after 2 min, red after 5 in play;
  - "No change for N min";
  - the verified/awaiting/unverified labels.

  The client only renders and polls.
- **Auto-refresh.** The client polls `/api/me/live` every 15 s while any card is live, the same
  cadence PT used via `st.fragment`, and stops when nothing is active. Reuse the `LiveNow`
  polling pattern.
- **Non-NFL legs:** "Not tracked live: settles after the game" (unchanged PT rule).
- **All ESPN breakers open:** a page-level notice, "Live data unavailable since HH:MM — results
  will still settle after the game."
- **Settled section:** slip result chips (won/lost/push/void/cashed out), payout, per-leg
  verification labels.
- **Empty state.**
  - The message: "Nothing to watch right now."
  - A primary CTA, "Log a slip", and a secondary CTA, "From a screenshot".
  - A nice touch: today's NFL games from Juju's own `games` table, so the page is never dead on
    a game day.

### 5.4 Log page (`/me/log`): spec

Mobile-first, one screen, built for thumbs. PT's target was an unplaced single in ≤10 seconds.

- **Sport,** remembered in a cookie.
- **Game:** a searchable picker fed by `/api/me/games?date=`. That's ESPN's scoreboard of the
  day, cached 10 min server-side, with a day selector defaulting to today ET.
- **Legs:** repeatable rows of Market (12 types, grouped) → Player (roster typeahead, reusing
  the `SearchBox` interaction) or Side (home/away, with team names) → Line → Odds. Plus "Add
  leg".
- **Slip type** is inferred exactly as PT does: 1 leg is a single; several in one game an SGP
  (toggle); anything else a parlay.
- **Slip odds:** singles take the leg's odds. Parlays compute fair combined odds from the legs
  (`core/odds.py`), editable. SGPs are entered.
- **Also on the form:** Book, Placed? (with Stake and an optional potential payout), Boosted,
  Tags (most-used first, inline "new tag"), Notes.
- **Saving.**
  - Save sends `POST /api/me/slips`, which calls `services.create_slip`.
  - Validation errors sit next to their fields.
  - PT's non-blocking warnings and duplicate detection (`find_duplicates`, `slip_warnings`)
    render as an amber strip with "Save anyway", never a silent block.
- **Below the form:** "Recently logged" (last 5).
- **Screenshot entry.**
  - A camera-roll upload button at the top of the same form.
  - Upload sends `POST /api/me/screenshot`, which runs PT's `prepare_image`, `Extractor` (Qwen
    via OpenRouter) and `resolve_slip`, and returns the same form pre-filled: doubtful fields
    highlighted, the image shown alongside.
  - Any failure falls back to the empty form with "Couldn't read this slip. Enter it manually."
  - With no `QWEN_API_KEY`, the panel says so and hides the uploader (exactly PT's behaviour).

### 5.5 Integration UX only the combined app can offer (build in Phase 5)

These are the payoff of merging. They make `/me` feel like part of Juju rather than a bolt-on.

- **"Log this parlay" on the public parlay page** (`/g/[game]/parlay`). It takes the tray's legs
  (they already carry market, line and player identity in the URL) and opens `/me/log`
  pre-filled. That turns a hypothetical T-45 parlay into a logged real bet in two taps. The odds
  field is focused, since the price you actually got is yours to enter.
- **"See the $10 card"** on every Watch or settled leg whose game Juju has archived: a deep link
  to `/g/[game]/[player]?card=…`.
- **Fair-value context on settled slips.** When Juju's archive holds the T-45 price for a leg's
  market, player and line, the slip's leg shows it beside the real odds ("$10 at T-45 would have
  paid …"). The two products explain each other.
- **Closing-line enrichment.** PT captured closing lines only via the Odds API; Juju's archived
  snapshots are another closing-price source for NFL legs. Licensing: this is the user's own
  logged bet, behind auth, one leg at a time, so inside the existing rule. Call it out in
  `docs/licensing.md` when built.

### 5.6 Design system

- Use Juju's tokens, the `Big_Shoulders` display font, its card chrome, chips and freshness
  colours. No new CSS framework and no component library.
- Tables on `/me/stats` get one small reusable `<table>` style (Juju has none yet).
- Everything must work at 360 px width (Playwright runs at phone width, as CI does today).
- Dark and light both, as today.

## 6. Backend design

### 6.1 Package layout

Everything PT-specific lives in a new subpackage. It mirrors PT's own structure, so ported tests
map 1:1.

_Reconstructed (the original block was lost), as built in Phase 1 plus what later phases add:_

```
backend/juju/
  core/  ingest/  worker/  api/       Juju's, shared where marked below
  tracker/
    models.py        PT core/models.py on Juju's Base (+ the tracker's enums, LegSource)
    markets.py       which markets each sport supports; NO_AUTO_CLOSING
    schemas.py       the validation gate (LegIn, SlipIn) and the screenshot reader's loose model
    settlement.py    legs and slips (uses Juju's settle_over / settle_spread)
    live.py          the Watch read model (uses Juju's freshness and status text)
    analytics.py     hit rate, CLV, ROI, dimensions, filters; loaders from Postgres
    services.py      the ONLY write path for slips and legs
    closing.py       closing-line selection
    resolve.py       Odds API keys, same_player, and the slip resolver (Juju's name matching)
    slip_import.py   the CSV importer and check-import
    fetch.py         ESPN scoreboards and rosters through Juju's router
    extraction.py    screenshot reading (Phase 4g)
    worker/          settle, finals, recheck, live legs, closing capture, canary, prune (Phase 2)
  api/me.py          /api/me/* (Phase 3)
```

Shared modules stay canonical in `juju/`, and the tracker imports them. Here is where each PT
module went:

| PT module | Destination | Why |
|---|---|---|
| `core/models.py` | `tracker/models.py` on Juju's Base | one metadata, one Alembic history; naming conventions already identical. **Done (Phase 1)**, with `LegSource` for PT's `DataSource` |
| `core/{schemas,services,analytics,live,settlement}` | `tracker/*` | PT-specific logic, pure and heavily tested: ported whole. **Done.** `live.py` takes Juju's identical freshness and status-text helpers; `settlement.py` uses Juju's `settle_over` and `settle_spread` |
| `core/config.py` | `juju/config.py` | each setting arrives with the phase that reads it (§6.6) |
| `core/odds.py` | `juju.core.odds` | identical apart from the header |
| `core/db.py` | `juju.db` | same engine and `session_scope` machinery |
| `ingest/http.py` | `juju.ingest.http` | 2-line diff (User-Agent `Juju/1.0`, import); keep Juju's UA: one honest identity |
| `ingest/espn.py` | `juju.ingest.espn` | scoreboards, rosters and status mapping are identical and multi-sport already (Phase 1 added PT's NBA/NHL/MLB tests and fixtures). The box-score parser takes a column table; the tracker's (`tracker/feed.py`) reads its markets in four sports, the NFL's through Juju's own `Stat` columns (**done, Phase 2**). PT's web-app fetch helpers became `tracker/fetch.py` |
| `ingest/router.py` | `juju.ingest.router` | identical apart from NFL-only box scores. Phase 1 added an optional `sport` to `scoreboard()` and `roster()`; Phase 2 added `box_score(…, sport=, columns=)` for the tracker |
| `ingest/guards.py` | `juju.ingest.guards` | identical, except the plausibility bounds: Juju keys them by `Stat`, PT by `MarketType`. The tracker's table is in `tracker/feed.py` (**done**) |
| `ingest/odds_api.py` | `juju.ingest.odds_api` | the models are a superset (closing uses them). The client gained an optional `sport_key`; the tracker asks for Juju's book chain, not `regions=us` (**done**, see Phase 2) |
| `ingest/nflverse.py` | Juju's streaming client; drop nflreadpy | Juju deliberately avoids polars and pandas (the worker has 512 MB). Its release-CSV reader covers schedule, players, weekly stats and play-by-play, and now snap counts and PFR ids for the tracker (`tracker/nflverse.py`, **done**). This removes PT's heaviest dependency |
| `ingest/resolve.py` | `tracker/resolve.py`, importing Juju's name matching | **done.** Juju's `normalize_name`, `same_team`, `same_game`, `player_key`, `team_aliases`, `mentions` and `match_roster_player` are the same rules; Juju's `PlayerMatch` says `espn_athlete_id` |
| `ingest/{closing,slip_import}.py` | `tracker/*` | tracker-only. **Done**, and the importer runs from `juju.cli` |
| `ingest/extraction.py` | `tracker/extraction.py` | Phase 4g, with the screenshot route |
| `worker/settle.py`, `worker/live.py`, the closing part of `worker/jobs.py` | `tracker/worker/*` | registered into Juju's single scheduler (6.4), as `tracker_*` jobs (**done**). `heartbeat` and `guarded` are identical to Juju's, which are used |
| `cli.py` (import-slips, check-import, backfill, export-recording, export-sample, read-slip) | subcommands of `juju.cli` | one CLI. **import-slips, check-import, tracker-backfill, export-sample and export-recording are done.** `read-slip` comes with the reader (4g). PT's `backfill` is `tracker-backfill`: Juju's `backfill` is a different job |
| `app/**` (Streamlit), `.streamlit/`, `Dockerfile`, `deploy/` (except backup/restore), `tests/app/**` | not ported | replaced by Next.js and Juju's stack. Their logic is listed in the checklists under Phases 3–4 |

Every ported file keeps Juju's convention: a `Ported from parlaytracker@c3bd43c <path>` header,
plus a line saying what changed.

### 6.2 Dependencies (`backend/pyproject.toml`)

- **Phase 1 added none:** rapidfuzz was already Juju's.
- **Phase 4g adds Pillow** (screenshot prep). It also decides how to call Qwen through
  OpenRouter: PT used `openai>=3,<4` (which ships its own HTTP library, `httpx2`), while Juju's
  LLM fallback uses plain httpx.
- **Never added:** streamlit, pandas, nflreadpy. They all die with the Streamlit app; analytics
  is pure Python, and nflverse stays streaming.
- **The dependency guard** (`tests/unit/test_dependencies.py`, ported in Phase 1) fails if
  `juju/` imports anything `pyproject.toml` doesn't declare.
- **shellcheck-py** moves to Juju's dev extra when the deploy scripts are adopted (Phase 6).

### 6.3 Database: one DB, Juju's Alembic history

**One Postgres database (`juju`).** Migration `0006_tracker_schema` (**done**) creates
`sportsbooks`, `events`, `slips`, `legs`, `tags`, `leg_tags` and `raw_samples` in their final
shape. It folds in PT's 0002 (tag `retired`) and 0003 (extra player markets), and seeds PT's
four sportsbooks.

Collisions and links:
- **`source_health`** exists in both, with the same shape. Keep Juju's one table:
  - PT's Breakers write and read the same rows;
  - the sources `espn_web`, `espn_site`, `espn_cdn`, `nflverse`, `odds_api` and `worker`
    coexist with Juju's `archive` row;
  - the health banner and `/api/status` read one table.
- **`events` vs `games`.** `events` (the tracker's, four sports) and `games` (Juju's, NFL) stay
  separate in Phases 1–4: different life cycles, no risky coupling. They join naturally on
  `espn_event_id`, and Phase 5 uses that join for the shared live poll and the cross-links. A
  later option, not planned now: tracker NFL legs referencing `games` directly.
- **Enums** are VARCHAR+CHECK per table, so values that differ between PT and Juju cost
  nothing: each table keeps its own CHECK. Leg sources use the tracker's `LegSource` (PT's
  values, with `manual`). Juju's `DataSource` has `odds_api` and no `manual`, and its CHECKs on
  Juju's tables are untouched.
- **`raw_samples`** is new to Juju (PT-only today): no conflict.
- **Advisory locks:** Juju's worker keeps its lock key, and PT's key retires (one worker
  process).
- **Migrations follow Juju's rules exactly:** never edit an existing one, read the generated
  file, run `tests/db/test_migrations.py`. That test now also compares the migrated catalog
  (CHECKs and server defaults included) with `create_all`'s, as PT's did.

### 6.4 Worker: one scheduler, union of jobs

Juju's `worker/__main__.py` gains the tracker jobs. There is one `Breakers`, one `EspnRouter`
(with `sample_sink` and `RECORD_EVENT_IDS`), and one `OddsApiClient` behind one key and one
reserve.

| Job | Source | Trigger | Notes |
|---|---|---|---|
| heartbeat | merge | 60 s | one heartbeat row; the health banner reads it |
| sync_schedule / sync_rosters | Juju | as today | rosters now also feed the Log form's typeahead |
| poll_live | Juju | 30 s | unchanged in Phases 1–4 |
| poll_nfl_live (tracker legs) | PT | 30 s | separate in Phases 1–4. Phase 5 merges the two loops: one scoreboard per game day, box scores for (hot games ∪ games with pending legs). That's fewer ESPN calls than either app alone |
| capture_t45 / repair_gaps / check_captures | Juju | as today | |
| capture_closing | PT | 60 s | spends credits: see the budget below |
| check_finals / settle / recheck_settled | PT | 15 / 5 / 60 min | |
| verify_games | Juju | 10:07 & 16:07 ET | unchanged |
| verify_nfl (legs) | PT | 10:00 ET | a Phase 5 candidate: one nflverse pass feeding both `games.verified_at` and `legs.verified_at` |
| canary | PT | 09:00 ET + startup | |
| prune_samples | PT | 04:00 UTC | |

**The Odds API budget is real money.** The combined worker runs T-45 captures, gap repair and
closing-line captures off one key. Juju's `odds_api_reserve` default is 1000; PT's was 50.
- The unified Settings get one `odds_api_reserve`, plus a per-feature guard: closing capture
  stops when the reserve is hit (PT's behaviour), and the health row says so.
- The owner's planned Juju-only 100K key (HANDOVER, "Needs the owner" #1) is now a hard
  prerequisite for running both capture loops. Until it exists, run with capture off, as today.

### 6.5 API: `/api/me/*` (new router `juju/api/me.py`)

- **Identity.** Every route requires the tracker identity (see §7). The web layer forwards it
  as `x-juju-user: <username>` after verifying the cookie. That's the same trust model as
  today's `x-juju-verified` header: the backend has no public address.
- **Read models are built server-side** from PT's pure functions (`tracker/live.py`,
  `tracker/analytics.py`), so the client never computes betting maths.

| Endpoint | Method | Backed by |
|---|---|---|
| `/api/me/live` | GET | the `tracker.live` read model over open slips (Watch page; 15 s poll) |
| `/api/me/slips` | GET | `recent_slips` / `open_slips` + status filter, settled in the last 7 days |
| `/api/me/slips` | POST | `services.create_slip` (+ `find_duplicates`, `slip_warnings` in the response) |
| `/api/me/slips/{id}/payout` | POST | `enter_slip_payout` |
| `/api/me/slips/{id}/cash-out` | POST | `mark_cashed_out` |
| `/api/me/legs/{id}` | PATCH | `settle_leg_manually`, `reopen_leg`, tags |
| `/api/me/legs/{id}/closing` | POST | `set_closing_line` (manual) |
| `/api/me/review` | GET/POST | `review_queue` / each action via services |
| `/api/me/analytics` | GET | `load_leg_rows`/`load_slip_rows` → `summarize`/`money`/`group_*` as JSON (dimension and filters as query params) |
| `/api/me/games?date=&sport=` | GET | the scoreboard for the Log form (10-min cache). PT's rule is worker-only provider calls; this reads through the same guarded router (`tracker/fetch.py`), like lookup's LLM exception, which is documented. Extend that note |
| `/api/me/roster/{event_id}` | GET | the roster typeahead (12-h cache) |
| `/api/me/screenshot` | POST | multipart ≤8 MB → `prepare_image` → `Extractor` → `resolve_slip` → prefill JSON. 45 s upstream timeout; the tracker proxy route needs a longer `AbortSignal.timeout` than the public proxy's 8 s |
| `/api/me/settings/tags`, `/sportsbooks` | GET/POST/PATCH | tag and book management via services |
| `/api/me/health` | GET | `source_health` rows + heartbeat age, for the banner |

`/api/status` (public) stays as it is. It must not gain tracker data.

### 6.6 Config (one Settings)

Phase 1 needed no new settings: PT's core takes them as arguments, and PT read them only in its
worker and app. Each one arrives with the phase that reads it:

| Setting | Phase | Notes |
|---|---|---|
| `record_event_ids` | 2 | done: `RECORD_EVENT_IDS` |
| `display_tz` | 3 | IANA-validated, PT's semantics. Give it a default (ET): PT required it, which would break Juju's existing deploys |
| `min_sample` | 3 | default 30 |
| `tracker_users` | 3 | §7 |
| `qwen_api_key`, `qwen_base_url`, `qwen_vision_model` | 4g | |

Keep `odds_api_reserve` single. PT's `allowed_logins` and `dev_login` die with Tailscale auth,
replaced by passcode users; local development gets a `DEV_TRACKER_USER` equivalent instead.

## 7. Auth & privacy: per-user passcodes

Extend the existing gate: same crypto, no new secrets infrastructure.

- **Env.** `TRACKER_USERS="alice:<passcode>,bob:<passcode>"` in `deploy/laptop/.env`. `up.sh`
  generates both passcodes, as it already generates `SITE_PASSCODE`, and prints them once. Two
  users, as the product specifies; the username becomes `logged_by`.
- **Cookie.** `juju_tracker = <username>.<expires>.<mac>`.
  - The mac key derives from that user's passcode (the existing `signedPass.ts`, with the label
    `juju-tracker-cookie`), so changing a user's passcode signs out only that user. That's the
    same property the site gate has.
  - 30-day expiry, `httpOnly`, `secure`, `sameSite=lax`, `path=/`.
- **Unlock.**
  - `/me/unlock/check` mirrors `/unlock/check`.
  - A small form ("This section is for the two of you — enter your name and passcode") is
    checked by a timing-safe compare against every configured user, then sets the cookie and
    bounces to `safeNext`.
  - A wrong passcode gets a plain, calm error.
- **Gate** (`proxy.ts` + `lib/passcode.ts`).
  - Any path under `/me` (except the unlock check) or `/api/me` requires a valid tracker cookie,
    in addition to the site passcode when one is set.
  - Pages get the HTML unlock form; API calls get JSON `401 { locked: true }`, the existing
    convention client fetches already understand.
- **Identity to the backend.** The `/api/me` proxy route verifies the cookie and forwards
  `x-juju-user: <username>`. Backend handlers take `logged_by` from it. The backend stays
  compose-network-only.
- **Fly launch compatibility.** When Juju goes public, `TRACKER_USERS` is simply unset on Fly
  (or set to the owners' codes). `/me` is inert without it; nothing public changes. Turnstile
  is not required for `/me`: a passcode is stronger than a CAPTCHA for a two-user section.
- **Licensing guard.**
  - Extend `tests/db/test_api.py`'s route census: list the `/api/me/*` routes, marked private
    (auth required).
  - Assert they are not reachable through the public allowlist in `web/app/api/[...path]/route.ts`.
    They get their own `web/app/api/me/[...path]/route.ts`.
  - The public allowlist regexes stay exactly as they are.
  - `legs.closing_odds` and `closing_line` are Odds API prices, so no `/api/me` endpoint may
    list legs across games for anyone but their owner.
- **Privacy.** Slips carry stakes and account-adjacent detail. Tracker data never appears in
  share images, OG tags, sitemaps, `/api/status`, or any cached public response: every
  `/api/me` response is `no-store`.

## 8. Deploy & laptop cutover (`deploy/laptop/`)

- **compose:** unchanged topology. The worker and api images pick up any new dependencies via
  the backend Dockerfile; no new service.
- **`.env` additions:** `TRACKER_USERS`, `DISPLAY_TZ`, `MIN_SAMPLE`, optional `QWEN_API_KEY`,
  optional `RECORD_EVENT_IDS`. Rename nothing existing.
- **`up.sh` gains:**
  - generating the tracker passcodes on first run;
  - failing closed if `TRACKER_USERS` is malformed;
  - checking, alongside its existing checks, that `/me` answers 401 (locked) and the unlock
    flow works.
- **Backups (adopt PT's).** Port `deploy/backup.sh` and `restore.sh` (pg_dump + rclone) into
  `deploy/laptop/`, now covering the single `juju` database. It holds the tracker too, so the
  existing "backup restores once" drill covers real bet data. Optional: PT's systemd update and
  backup timers, if the owner wants auto-updates on the laptop (Juju is manual today).
- **Cutover** is simple, because it's a fresh start.
  1. Deploy the merged build with `up.sh`. Migration 0006 creates the tracker tables.
  2. If the owner wants the 25 historical slips back, copy the CSV to the laptop, never into the
     repo.
  3. Run `docker compose -f deploy/laptop/docker-compose.yml exec -T worker python -m juju.cli
     import-slips - --user alice < slips.csv` as a dry run, then again with `--apply`.
  4. Then run `check-import`, exactly as PT's HANDOVER describes.
- **Game-day acceptance** (from PT's Phase 3/4/7 exits, now run inside Juju):
  - log real slips from both phones over the internet;
  - watch the Watch page during a game;
  - verify settled legs against the book;
  - block `site.web.api.espn.com` mid-game, and watch failover and the red card state;
  - afterwards, `export-recording` a game into fixtures.
- **Retire PT's deployment:** stop its stack (if it was ever started), and archive the repo.

## 9. Testing & CI

- **Port PT's tests with the code.**
  - Unit tests for analytics, settlement, live rules, closing, extraction, `resolve_slip`,
    `slip_import`, and guards/breakers move under `backend/tests/unit/`. They test pure
    functions, so mostly only import paths change.
  - PT's `tests/db` suite moves under `backend/tests/db/`, on Juju's conftest (the same
    `TEST_DATABASE_URL` rules, the socket guard). That covers services, settle jobs, live
    recording and simulation, constraints, migrations, the import end to end, and
    canary/prune.
  - Phase 1 put the tracker's tests in `tests/unit/tracker/` and `tests/db/tracker/`, with
    shared helpers in `tests/tracker_support.py`.
- **PT's `tests/app` (Streamlit) is deleted.** Its intent is re-expressed as:
  - API tests for every `/api/me` route: auth required, `logged_by` honoured, service errors
    become 4xx with PT's messages;
  - Playwright specs under `web/e2e/`, at phone width like today's 17.
- **New web unit tests** (`node --test`, no `@/` imports): the tracker gate (cookie sign and
  verify, including the username binding), and `TRACKER_USERS` parsing.
- **New Playwright specs:**
  - `me-gate.spec.ts`: locked → form → wrong code → right code; API 401 JSON.
  - `me-log.spec.ts`: log a single and an SGP against the seeded game; the warnings strip.
  - `me-watch.spec.ts`: a seeded pending slip on the recorded live game. The card renders;
    leg states, freshness, the settled section.
  - `me-review.spec.ts`: settle manually, enter a payout.
  - `me-stats.spec.ts`: renders with seeded settled slips; low-sample greying.
- **Seed.** Extend `juju.cli seed` with a tracker variant that creates demo slips (one pending
  SGP on the recorded live game, a few settled) for e2e and local development. Same gotcha as
  today: the seeded live game goes stale after 5 minutes, so reseed before e2e.
- **CI.** The three jobs stay.
  - `backend` grows by roughly PT's suite. Raise its time limit by the existing 5× rule after
    the first green run.
  - `web` gains the new specs; raise its limit too.
  - `e2e` seeds tracker data as well.
  - PT's `deploy` CI job (compose smoke + backup/restore) becomes an optional fourth job, or
    folds into the `deploy-laptop` skill's container rehearsal.
- **Fixtures.**
  - PT's fixtures move under `backend/tests/fixtures/`: ESPN multi-sport, Qwen replies, the
    nflverse week-3 slice, the Odds API all-markets file.
  - Dedupe on content: all ten PT files that share a path with Juju's are byte-identical.
  - Never re-fetch odds fixtures (credits).

## 10. Roadmap (each phase is one PR into `main`, in order)

Juju's PR discipline applies: start from the latest `main`, and update HANDOVER.md before
finishing.

### Phase 1: absorb the core (backend only, no behaviour change). Done, 2026-10-08

**What shipped:**
- `juju/tracker/` with models, schemas, services, analytics, live rules, settlement, closing,
  the resolver, the importer and the ESPN fetch helpers.
- Migration 0006, with the stricter migration test.
- The router's optional `sport`.
- `juju.cli import-slips` and `check-import`.
- The dependency guard.
- PT's tests: 445 unit and 75 database cases, all green, plus the multi-sport ESPN cases.

**What it found:** PT's `check_import` crashed on a disagreeing leg with no final value (a
void, or a result entered by hand). It was fixed in the port, with a test.

**Not ported yet, on purpose:**
- the 8 resolver tests that start from a model reply (Phase 4g);
- the 4 end-to-end import cases that settle through the worker's jobs (Phase 2);
- the settings tests of `test_config_and_markets` (with the settings);
- `test_analytics_format` (Phase 4f).

**Checked:**
- The importer was dry-run against live ESPN (free) for the recorded ARI @ SF game.
- No web, deploy or worker change.
- The public route census is unchanged.

### Phase 2: worker integration. Done, 2026-10-08

**What shipped:**
- The tracker's jobs in Juju's one scheduler (`juju/tracker/worker/`), named `tracker_*`:
  - the live poll for pending legs, finals, settlement and the 24-hour recheck;
  - nflverse verification at 10:00 ET;
  - the canary at 09:00 ET and at startup;
  - pruning, and closing-line capture when there is a key.
- One router, so one ESPN rate limit and one set of breakers. One Odds API client, so one key
  and one reserve.
- `RECORD_EVENT_IDS`, and a raw-sample sink on the shared router.
- `tracker-backfill` (it takes Juju's worker lock), `export-sample` and `export-recording`.
- PT's worker tests: settlement, the live poll, closing capture, verification, the live
  simulation and recording replay, canary/prune/CLI, the worker, the new markets and box
  scores, and the four settling import cases. All green, with no network in any test.

**How the Phase 1 notes were settled:**
- **Box scores: one parser.**
  - Juju's `espn.parse_box_score` takes a column table (Juju's `STAT_COLUMNS` by default), and
    the router's new `box_score(…, sport=, columns=, check_stats=)` reads any sport, without
    plays.
  - The tracker's table (`tracker/feed.py`) uses, for its 8 NFL markets, exactly the columns of
    the matching Juju `Stat`: one definition, not two. It adds NBA points and NHL goals +
    assists.
  - Its values are identical to PT's own parser on all 7 recorded box scores (NFL, NBA, NHL,
    the cdn wrapper, overtime). PT's rule holds: a player absent from the tables has no value.
  - `Stat` is untouched.
- **The jobs keep PT's calls.** Two thin adapters give them PT's interface over Juju's shared
  code: `EspnFeed` (`scoreboard(sport, day)`, `box_score(sport, event)`) and `OddsFeed`. So the
  jobs and their tests ported nearly verbatim.
- **nflverse.**
  - Juju's client gained two datasets of its own: `snap_counts`, and `player_ids` (espn_id →
    pfr_id, read from the players file under its own key, so a change there can't stop
    `VerifyGames`).
  - `tracker/nflverse.py` (`NflverseLegs`) has PT's `stat_value`, with its "every column empty
    is no stat line" rule, and `offense_snaps`. nflreadpy and polars are gone.
  - The fixture is a real slice recorded today (`scripts/record_nflverse.py --tracker`).
    PT's own slice had 0 in 21 values of the four markets it added on 2026-09-29 (completions,
    touchdowns, field goals); nflverse has the real numbers, which match ESPN. No test depended
    on those zeros.
- **The Odds API.**
  - Closing capture asks for Juju's book chain (Hard Rock first, at most 10 books: the cost of
    one region), not PT's `regions=us`, which never returned Hard Rock.
  - The client gained an optional `sport_key`; Juju's own requests are unchanged.
  - The capture is not priority: it stops at Juju's reserve (1000).
- **Raw samples.**
  - The sink is on the shared router, so Juju's own parse failures are kept too: the last 5 per
    provider, pruned daily.
  - Every response is recorded only for the games in `RECORD_EVENT_IDS`.
- **Locks and rows.** One worker, Juju's lock. PT's key retired. The heartbeat and `guarded` are
  Juju's, which were the same code.
- **ESPN budget.** The tracker's live poll makes no request unless a pending leg is on an active
  NFL game. Merging it with Juju's loop is still Phase 5.

**Checked:**
- The real worker ran for 150 s against a scratch database, with no Odds API key, while
  TB @ DAL was about to start.
- Every job ran without error, and the startup log line `up.sh` checks is unchanged.
- The canary parsed the latest finished NFL game from all three ESPN hosts and loaded all five
  nflverse datasets: every provider `ok` in `source_health`.
- Peak memory for the canary and Juju's nflverse check together: 98 MB, against the worker's
  512 MB.

**Not ported, on purpose:** PT's `tests/live/` (two `-m live` tests that call real services).
Juju's tests block the network, and the canary is the same check, run by the worker.

### Phase 3: auth + `/api/me`

- `TRACKER_USERS` settings.
- The `juju_tracker` cookie (sign and verify, with the username).
- The unlock page and its check route.
- The `proxy.ts` gate for `/me` and `/api/me`.
- The `web/app/api/me/[...path]/route.ts` proxy: longer timeouts, 8 MB for the screenshot
  route, `no-store`.
- The `juju/api/me.py` router with all endpoints.
- The licensing-census test extended: private routes, not publicly allowlisted.

**Exit:**
- API tests green.
- With a passcode set, `/me` and `/api/me` are locked from the outside and open with the right
  user.
- `logged_by` is the username on created slips.

**Estimate:** 2–3 sessions.

**Checklist: PT logic that lived only in the app and belongs in the read models** (PT paths at
c3bd43c):
- **The health banner** (`app/common.py:127-158`): the worker has never run, or its heartbeat is
  more than 3 min old; Odds API credits below the reserve plus 10; a breaker open with SCHEMA
  shows an error even after its open time; a half-open provider is silent; otherwise a provider
  failing for more than 5 min warns. PT's 8 tests for it are `tests/app/test_app.py:230-286`.
- **Formatting** (`app/common.py:22-103`): `MARKET_LABELS`; `fmt_time` (display time zone);
  `fmt_odds`; `fmt_money` (dollars, or "u" for unplaced units); `fmt_line`; `leg_summary`;
  `slip_summary`.
- **ESPN lookups for the Log form** (`app/common.py:116-124`): scoreboard cached 10 min, roster
  12 h. An interactive request may wait up to 5 s for a rate-limit slot (`WEB_MAX_WAIT`); the
  worker never waits.
- **Who logged it** (`app/auth.py`): replaced by the passcode users; the name becomes
  `logged_by`.

### Phase 4: the `/me` UI (native Next.js)

The order inside the phase, each step shippable:
- (a) the `/me` layout, sub-nav and health banner;
- (b) Watch;
- (c) Log, with the games and roster endpoints wired, and the warnings/duplicates UX;
- (d) Review;
- (e) Settings;
- (f) Stats;
- (g) Screenshot upload, last: it degrades to the manual form until a Qwen key exists.

Seed data and Playwright specs land with each page.

**Exit:**
- All six PT pages have native equivalents, with parity on the PT SPEC §9 rules: cadence text,
  freshness colours, verification labels, CLV-first analytics, low-sample greying.
- Phone width; light and dark.
- e2e green.

**Estimate:** the largest phase, 4–6 sessions. (b) and (c) are the ones that make the feature
real: ship them first.

**Checklist: PT page logic to carry over** (PT paths at c3bd43c):
- **Watch** (`app/pages/live.py`):
  - `leg_line` (40-54): "no stat line yet" for a player vs "waiting for a score" for a team;
    "covering"/"not covering" for alt spreads.
  - `fmt_value`, `fmt_age` (30-37): "N s ago" under 120 s, then minutes.
  - The colours and state words (20-23); the refresh every 15 s; the empty-state and
    "unavailable since" wording.
- **Log** (`app/components/slip_form.py`):
  - **Slip type** (`_slip_type`, 162-167): one leg is a single; all on one game an SGP unless
    switched off; otherwise a parlay.
  - **Slip odds** (`_computed_parlay_odds`, `_slip_odds`, 170-183).
  - **Form → `SlipIn`** (`_build`, 186-216): boosted only for non-singles; stake and payout
    dropped when unplaced, rounded to cents. Plus `_read_leg` (136-159) and the "Leg N: …"
    messages (`_messages`, 219-230).
  - **The kickoff warning** (`_live_warnings`, 424-437).
  - **Known PT bug, don't port it:** `_save` (233-261) passes the scoreboard's status to
    `upsert_event`. A slip logged after its game ended then stores the game as `final` with no
    `final_at`, so it never settles. The importer stores `scheduled` for exactly this reason
    (PT HANDOVER decision 28).
- **Review** (`app/pages/review.py`): `RESULT_CHOICES`, `KIND_TITLES` (13-19), the
  required-field messages (48-72), and the help text that an alt spread's final value is the
  team's winning margin (85-87).
- **Stats** (`app/pages/analytics.py`):
  - `pct`, `signed`, `hit_rate_text`, `evidence_text`, `clv_text` (31-55).
  - The tables (58-89): CLV columns first, "Break-even (n k)" when it covers fewer legs, the
    "low sample" note, greyed rows (92-95).
  - Filters only when a dimension has more than one bucket; the any/all tag toggle only with 2+
    tags (105-121).
  - Default grouping by sport (153).
  - PT's `tests/unit/test_analytics_format.py` (10) and `tests/app/test_analytics_page.py`
    (11) hold the wording.
- **Settings** (`app/pages/settings.py`) and the nav's review badge (`app/main.py:12-13`).
- **Screenshot (4g):**
  - `process_upload` (`app/pages/screenshot.py:46-59`) never raises: every failure becomes
    `CANT_READ`. Each image is read once (sha256, 104-114).
  - Port `ingest/extraction.py` and `tests/fixtures/qwen/` with it, and the 8 `resolve_reply`
    tests of PT's `tests/unit/test_resolve_slip.py`.

### Phase 5: convergence and cross-links (the merge pays off)

- One live loop: hot games ∪ pending-leg games, one scoreboard a day.
- One nflverse verify pass, feeding games and legs.
- "Log this parlay" from the public parlay page.
- "See the $10 card" from tracker legs.
- T-45 fair value on settled slips.
- Optional, documented in `licensing.md`: closing-line enrichment from Juju's archive.

**Exit:**
- A measurable drop in ESPN requests per game day (log before and after).
- Links work both ways.
- The licence census and the licensing doc updated.

**Estimate:** 2–3 sessions.

### Phase 6: deploy, accept, retire

- `up.sh`, `.env`, README and skill updates.
- Adopt backup and restore, and do one real restore drill.
- Deploy to the laptop.
- Game-day acceptance (§8) with real slips from both phones.
- Archive `dhynesmnk-cyber/parlaytracker`. Its README says "Absorbed into Juju — see
  juju/docs/plans/integrate-parlaytracker.md"; keep it public as it is today, since it contains
  no secrets.
- Update Juju's docs:
  - HANDOVER.md and CLAUDE.md;
  - `docs/GOALS.md`: §8 "still binding" becomes "fully absorbed";
  - `docs/deploy.md`: Fly with `TRACKER_USERS` unset, or owners only.

**Exit:**
- A real weekend of slips logged, watched and settled inside Juju.
- PT's stack stopped.
- The repo archived.

**Estimate:** 1–2 sessions, plus one real game weekend (owner).

**Total:** roughly 12–20 working sessions, with a usable Watch and Log after Phases 1–4b/4c.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Odds API credits: two capture loops on one key (T-45 + repair + closing) | a single reserve, a per-feature guard, `quota_remaining` in the banner; the owner's dedicated key is a prerequisite for turning captures on (already "Needs the owner" #1) |
| ESPN rate limits and blocking: two 30 s pollers | shared breakers and failover from day 1 (Phase 2); Phase 5 merges the loops, ending with fewer requests than the two apps made separately |
| Worker memory (512 MB) | drop nflreadpy, polars and pandas; Juju's streaming nflverse reader; analytics JSON built from pure Python |
| Diverged shared modules (espn, guards, resolve, settlement, live) merged wrong | merge only where diffs are provably safe (Phase 1 shared only identical rules, each checked); Juju's stays canonical for cards, PT's logic lives in `tracker/` and imports the shared bits; each merge carries both repos' fixtures and tests |
| Streamlit → Next fidelity (analytics density, form warnings, freshness rules) | all rules live in ported pure Python with their exact-number tests; the UI renders read models; the Phase 3–4 checklists list what only the app held; Playwright specs encode the SPEC §9 behaviours |
| Public/private leakage (stakes, slip data, licensing) | `/me` and `/api/me` fully gated, `no-store`, absent from status, share images and OG tags; the census test asserts the public allowlist never grows; e2e asserts 401 without the cookie |
| Two passcodes confuse | the unlock copy distinguishes them; the site gate is unchanged; the tracker unlock only ever appears under `/me` |
| e2e staleness (the seeded live game dies after 5 min) | the existing gotcha, now also for seeded slips: reseed before e2e, documented in the spec file |
| CI time limits trip | raise them by the 5× rule after the first green runs (HANDOVER convention) |
| Scope creep vs M5 (Juju's own launch hardening) | this plan is additive; M5 items (k6, Lighthouse, snapshots) are unaffected except for CI minutes; the owner can interleave |

## 12. Repo absorption & history

- **Recommended, and what Phase 1 did:** copy with provenance headers, Juju's existing
  convention (`Ported from parlaytracker@c3bd43c <path>`). Files are restructured on the way in,
  and a subtree merge would preserve history only for paths that immediately move. The source
  SHA is recorded in HANDOVER.md.
- **The alternative, if the owner wants full history:** `git remote add parlaytracker …` and
  `git subtree add --prefix=backend/_parlaytracker_history`. It costs little and keeps blame.
- **Either way,** archive the parlaytracker repo after Phase 6, not before: it is the reference
  until the merge is accepted. Its HANDOVER's open exit criteria (real-world verification for
  Phases 2/3/4/6/7) transfer to Juju's Phase 6 game-day acceptance.

## 13. Needs the owner

1. **A Juju-only Odds API key (100K plan).** It now funds T-45 capture, repair and closing
   lines. Until then the tracker works fully with manual closing lines, PT's designed fallback.
2. **Two usernames** (Phase 3). `up.sh` generates the passcodes, which are printed once and
   stored in `.env`.
3. **Optional:** an OpenRouter key (`QWEN_API_KEY`, with training-permitted providers off) for
   the screenshot reader. The Log form works without it.
4. **A backup destination** (an rclone remote) and one restore drill, now covering real bet
   data.
5. **One real game weekend** for Phase 6 acceptance: both phones logging and watching.
6. **Approve archiving `dhynesmnk-cyber/parlaytracker`** at the end. It is public today, and it
   stays secret-free.
7. **Decide** whether the laptop gains PT's auto-update and backup systemd timers or stays
   manual.

## 14. Not in scope (now)

- No data migration: a fresh start, with the CSV importer available.
- No Tailscale-identity auth: passcodes replace it (Serve still works for tailnet access).
- No under-side or new-market analytics: PT's Over-only product rules carry over unchanged.
- No more than two users; no accounts or registration.
- No changes to the public site's behaviour, pricing rules or worlds, beyond the Phase 5
  cross-links (which are additive and gated on the tracker cookie).
- The Fly.io launch itself (M5) is unchanged; `/me` rides along inertly until `TRACKER_USERS`
  is set.
