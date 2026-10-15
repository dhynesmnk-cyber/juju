# Plan: fix lookups, missing prices and the UI after the 2026-10-11 live test

**Status:** drafted 2026-10-12. **Stages 1 and 2 are done** (2026-10-15). Stage 3 is next.
**Target:** `juju@0b29db3` and onward.

> **Changed 2026-10-15, by the owner:** a 20K-credit key instead of the free one; captures at
> T-3h and T-45 only (the T-45 one from T-46, retried until T-45:00); score polling cut by 75%
> (every 4 minutes) instead of only when ESPN lags; last weekend's missed games filled from the
> vendor's history once; and "build it all now". The credit budget below stays, as the safety
> net: on 20K every game gets the full plan. Its tiers became Full (both captures), T-45 only,
> Lean and None. The rest of Stage 1 was built as written.

## Context

On Sunday 2026-10-11 the owner ran the laptop preview (Tailscale Funnel) with a **free Odds
API key** (500 credits a month). Three complaints: "What just happened?" lookups weren't
accurate, heaps of things had no price, and the UI is janky and unhelpful. Three read-only
searches of the code, plus my own checks, found clear causes for all three (below).

The owner's decisions (2026-10-12):
1. **Prices:** build everything and test it on the free key first. A paid plan comes later,
   so the capture must ration credits on a small quota and capture everything on a large one.
2. **No name in the text:** offer the latest plays to tap, newest first. Never pick one
   automatically.
3. **UI:** a bigger redesign, with mockups for the owner to choose from before building.

Branch `claude/dazzling-newton-n3hzs2`. Ship as stages, one PR each: Stage 1 before the next
game day (TNF, Thu 2026-10-15), Stage 2 right after. After each PR merges, restart the branch
from `main` (HANDOVER: never push to a branch whose PR merged).

## Stage 0: evidence from the laptop (first, no code)

The owner pastes this output into the session. It confirms which causes hit on Sunday and
shows how many credits are left. Read-only, counts and names only, no prices:

```bash
cd juju && docker compose -f deploy/laptop/docker-compose.yml exec -T db psql -U juju juju <<'SQL'
SELECT source, state, quota_remaining, last_error FROM source_health;
SELECT g.away_abbr||'@'||g.home_abbr AS game, g.commence_time, s.slot, s.observed_at, s.cost
  FROM games g LEFT JOIN odds_snapshots s ON s.game_id = g.id
 WHERE g.commence_time >= '2026-10-11' ORDER BY g.commence_time, s.observed_at;
SELECT game_id, status, count(*) FROM player_name_map GROUP BY 1, 2 ORDER BY 1, 2;
SELECT path, count(*) FROM lookups WHERE created_at >= '2026-10-11' GROUP BY 1;
SELECT created_at, path, text, result FROM lookups
 WHERE created_at >= '2026-10-11' ORDER BY created_at;
SELECT game_id, count(*) FILTER (WHERE espn_athlete_id IS NULL) AS no_player, count(*)
  FROM plays GROUP BY 1;
SQL
docker compose -f deploy/laptop/docker-compose.yml logs worker --since 30h \
  | grep -E "breaker open|not enough Odds API credits|unmapped|roster .* unavailable" | tail -50
```

If the free quota is already spent, it won't refill until the monthly reset. Until then,
testing on the laptop covers lookups and the UI, and the site will say plainly that prices
are paused.

## Stage 1: prices (backend, deploy)

### What went wrong (confirmed in code)
- `worker/live.py:220-235` `_odds_scores`: 1 credit **every 60 s while any game is on**, with
  no budget check. Its result is only used for games whose ESPN reading is over 90 s old
  (`live.py:237`), so with ESPN working every call is wasted. About 600 credits on a Sunday:
  more than the free month.
- `config.py:33` reserve 1000 > 500. With a free key the T-3h and T-60 slots and repair never
  run (`capture.py:68-72`). T-49, T-47 and T-45:30 each ask for all 34 markets (~29 credits a
  call), and 1 pm ET has about 9 games, so roughly 780 credits.
- `worker/archive.py:58`: only the **first** name per snapshot that previously failed to
  match is retried. Prices with an unmatched name have no player, so those players get no
  cards. Rosters first load 60 minutes after the worker starts (`__main__.py:68`, no startup
  run), so captures soon after a restart store names unmatched.
- `t45.py:87-88` says `NO_SNAPSHOT` ("no archived odds for this game") when the game has
  snapshots but not that market.
- `views.py:231-238` prints "None" as the line ("CHI None", "Over None points").
- `/api/status` (`app.py:164-179`) and the UI never say why prices are missing (no key,
  credits used up, bet type not saved). `up.sh` accepts a key that doesn't work.

### Changes
1. **Score polling only when it's used** (`worker/live.py` `_odds_scores`): call `/scores`
   only when a game in play has an ESPN reading older than `ESPN_STALE`, and only if
   `affordable(quota, 1, reserve, priority=False)`.
2. **A capture budget** (new `worker/budget.py`, pure and unit-tested; used by `CaptureT45`):
   - Credits per game = (credits left) ÷ (games left before the reset). Games left is the
     larger of the known games with an Odds API id before the reset and a weekly estimate
     (new settings `ODDS_API_RESET_DAY`, default 1, and `GAMES_PER_WEEK`, default 16).
   - Choose a plan for each game from that budget:
     - **Full:** today's slots and markets.
     - **One full:** a single on-time capture of `ALL_KEYS` at T-47, retried every 20 s until
       T-45:00. Later slots are skipped once an on-time snapshot exists.
     - **Lean:** that single capture with the first *k* markets of a new
       `CAPTURE_PRIORITY` in `core/markets.py`: h2h, spreads, totals, anytime TD, rush,
       receiving and pass yards, receptions, pass TDs, team totals, then the yard ladders,
       first TD, and the rest.
     - **None:** the game is skipped and the reason is recorded.
   - About 7 credits a game covers the most-asked bets and roughly fits 500 a month. A paid
     key lands in **Full** with no change.
   - Record each game's plan and reason on the game: migration 0007, `games.capture_plan` and
     `capture_note`. Then read the generated file and run `tests/db/test_migrations.py`.
3. **Name matching** (`archive.py`):
   - Retry every unmatched name, not just the first.
   - New `remap_prices(session, game)`: after a roster sync, and before each capture,
     re-match unmatched and doubtful names. Point their stored `Price` rows (which keep the
     raw name) at the player. That changes only which player a stored price belongs to, never
     a number.
   - Add `sync_rosters_at_startup` in `worker/__main__.py`.
4. **No-price reasons** (`core/t45.py`, `api/views.py`):
   - New `NoPrice.NOT_SAVED`, for a market missing from `OddsSnapshot.markets` (rationed),
     separate from `NOT_OFFERED` (asked for, no book listed it).
   - `NO_SNAPSHOT` only when the game truly has none, with its text taken from
     `capture_note`: "no key", "credits used up until 1 Nov", "worker was off".
   - Plain-English texts, without "T-45".
5. **Never print "None"** (`views.py` `_bet_text`): a bet with no line reads "CHI spread",
   "Game total" and so on. Add a test that no card text contains "None".
6. **Say it on the site** (`app.py` `/api/status`): add `prices: on | rationed | paused | off`
   with a short message (from source_health quota and the capture plan). The site shows it on
   the home page (Stage 3 places it).
7. **Deploy:**
   - New CLI `odds-status`: one free events call; prints key OK, credits left, reset date and
     today's plan. Never prints the key.
   - `up.sh` runs it after start and stops with a clear message if the key is rejected.
   - Update `deploy/laptop/README.md`: a free key works, rationed.
   - Add the `report --since DATE` CLI: Stage 0's queries as a command for every test weekend.

Reuse: `affordable` and `Slot` (`capture.py`), `match_roster_player` (`ingest/resolve.py`),
`SourceHealth` and `Breakers.persist` (`ingest/router.py`), `check_captures` (`worker/jobs.py`).

## Stage 2: lookups and live plays

### What went wrong (confirmed in code)
- `api/lookup.py:162-228` never reads the `Play` table. With no name in the text ("what just
  happened", "that TD", "who just scored"), it ends at "We couldn't tell which player".
- `ingest/router.py:426-436,144-145`: **one** box score that fails to parse or a plausibility
  check shuts that ESPN host for 30 minutes, for every game. The same game is tried on all
  three hosts, so one odd game can make ESPN go dark for 30 minutes. That means no stats and no
  new plays.
- `worker/live.py:275-286`: a CDN read has no plays but still clears "score just changed", so
  the new play's chip waits for a later read.
- `worker/live.py:373-374`: a play stored without a player keeps none forever.
- `ingest/espn.py:483-485`: names like "A.St. Brown" break the shortened-name pattern.
- `ingest/espn.py:556-566,588`: only TDs, made field goals and 20+ yard runs or catches are
  kept, so interceptions and return TDs never appear.
- `worker/live.py:355-357`: return TDs are labelled "TD run".
- `ingest/parse.py:50-51`: "rushing/receiving touchdown" is read as the yards market. Words
  like that, who, last, did, happened and great stay in the name text and fuzzy-match
  surnames.
- `lookup.py:34-43`: the live group of games comes first, so a game that went final 5
  minutes ago is searched only after the live ones, and a lone live namesake wins.
  `TEAM_ALIASES` is empty (`resolve.py:29`): "Niners", "Bucs", "Philly" and "Birds" fail.
- A chip tap doesn't say which play it was. A TD catch puts the rush-yards cards first
  (`markets.py:44-45`).

### Changes
1. **Latest plays for an unnamed lookup** (`api/lookup.py`):
   - When no player or team resolves, return `kind: "plays"`: up to 8 notable plays from
     games in play and games final in the last 60 minutes, newest first (by `wallclock`).
   - Filter by the parsed play kind and by yards (±5) when given.
   - Each row: label, game, quarter and clock. No prices, so the licensing rule holds and
     `/api/lookup` stays the same endpoint.
   - `SearchBox` shows them as one-tap rows.
2. **Recent plays break ties:**
   - When a name is doubtful, a candidate with a matching play in the last 10 minutes
     comes first.
   - If exactly one candidate has such a play, pick him. This is a change to GOALS §7.3,
     recorded in HANDOVER as an agent decision.
   - The first group of games also includes games final in the last 60 minutes.
3. **The play itself:**
   - Chips and latest-play rows carry `play_id`. `/api/player/...` and `/api/team/...` take
     `play_id`, check it belongs to that game and player, and return `the_play` (label,
     quarter, clock, kind, yards).
   - A TD with a passer touches receiving and TD markets, not rushing.
4. **ESPN reliability** (`ingest/router.py`; it is ported, so its header says what changed):
   - A parse or plausibility failure benches **that game's** summary for 5 minutes.
   - The host closes only when two different requests fail that way within 10 minutes.
   - A CDN read with no plays keeps the "score just changed" priority.
5. **Plays** (`ingest/espn.py`, `worker/live.py`):
   - Re-match plays with no player on each read and after a roster sync.
   - Fix the shortened-name pattern.
   - Keep interceptions (QB and defender) and return TDs, with labels: "pick-six",
     "punt return TD".
   - Store the TD passer, so the QB's TD passes are chips too.
6. **Text** (`ingest/parse.py`, `ingest/resolve.py`):
   - "rushing/receiving touchdown" → anytime TD.
   - Add time and filler words to `_FILLER`.
   - Fill `TEAM_ALIASES` with common nicknames.
   - Ignore team abbreviations that are ordinary words ("no", "ten", "car", "min", "den")
     unless they are the whole text.
7. **Golden set** (`tests/fixtures/lookups/golden.jsonl`, `tests/db/test_golden.py`):
   - A multi-game slate built from the recorded rosters and summaries.
   - New lines: time-relative phrasing (expecting `plays`), namesakes in different live
     games, a game just gone final, "rushing touchdown", nicknames, parlay lines.
   - Raise the floors.

## Stage 3: UI redesign, mockups first

1. **Mockups** (an Artifact for the owner, with sample numbers labelled as samples): three
   directions, each at phone and desktop width:
   - **Feed-first:** home is one live feed of plays across games, with search on top.
     Tapping a play opens its bets.
   - **Game-first:** compact live scoreboards, then a game page with a timeline of plays.
   - **Answer-first:** one big answer for the play, then a short list of other bets.

   All three keep the owner's settled decisions: the worlds and the Jujus, the price
   rendered first, the book named on each card, the other books listed (behind "Details" is
   offered as an option), 21+, the "Hypothetical" line on each card, the footer. Slim cards:
   - bet, result and "$10 → $X at Hard Rock";
   - "Details" holds the other books, the value without the book's margin, and when it was
     priced;
   - the "Source record" line goes;
   - bets with no price become one line at the bottom.

   **Stop here for the owner's choice.**
2. **Fixes in any design:**
   - Times: server and phone render the same text. The server sends ET, the browser adds
     local time after mount.
   - `loading.tsx` and `error.tsx`; an outage no longer shows a 404.
   - No layout shift:
     - the typeahead floats over the page, with an AbortController, and closes when you tap
       away;
     - the status banner has reserved space;
     - the 21+ choice moves to a cookie, so the server knows it;
     - the count-up starts at $10, never the final number;
     - Live chips keep their place.
   - `aria-live` only on what changed.
   - OT instead of "Q5"; Share resets; tap targets of 44 px or more everywhere; a desktop
     breakpoint.
   - `about/page.tsx` is updated for parlays.
3. **Build the chosen direction:**
   - `web/app/page.tsx`, `components/LiveNow.tsx`, `SearchBox.tsx`, `ResultView.tsx`,
     `ResultCard.tsx`, `ParlayView.tsx` and `app/globals.css`, plus any new pages the
     direction needs.
   - Types in `lib/types.ts` follow the new API fields (`the_play`, `kind: "plays"`,
     `prices` status).

## Verification

- **Backend:**
  - `ruff check . && pytest` with `TEST_DATABASE_URL` (CLAUDE.md).
  - New tests:
    - budget tiers;
    - a simulated Sunday: 14 games at 1 pm, 4 pm and 8 pm with a fake `OddsSource` at 500
      credits. Every game gets an on-time lean snapshot, total spend stays within budget,
      and no `/scores` calls happen while ESPN is fresh;
    - every unmatched name is re-matched and its prices re-pointed;
    - rosters sync at startup;
    - `NOT_SAVED` and `NO_SNAPSHOT` texts;
    - no "None" in any card;
    - `/api/status` prices state;
    - latest-plays lookups and recent-play ties;
    - per-game ESPN benching, and the host still closing on repeated failures;
    - CDN priority, play re-match, the name pattern, return-TD labels;
    - the golden set on the slate.
  - The licensing guard in `tests/db/test_api.py` passes unchanged: no new endpoint.
  - Run `tests/db/test_migrations.py` after migration 0007.
- **Web:**
  - `npm run lint && npm run typecheck && npm test && npm run build`.
  - Playwright adds:
    - a run with `timezoneId: "America/Los_Angeles"` against the UTC server that fails on
      any hydration warning in the console;
    - a loading state on a chip tap;
    - an unnamed lookup offering plays;
    - unpriced cards at the bottom;
    - axe on player and team pages;
    - `toHaveScreenshot` per world with `NEXT_PUBLIC_WORLDS=off`.
- **End to end locally:** seed, uvicorn and `next start` as in HANDOVER "Gotchas". Take
  screenshots at 390 px and 1280 px for the owner.
- **On the laptop:** `git pull && deploy/laptop/up.sh`, then `odds-status` shows the key,
  credits and plan. After the next game day, `report --since <date>` shows the snapshots per
  game, matched names and lookups, compared with this Sunday.
- **Docs:** update HANDOVER.md each stage. Record:
  - the owner's three decisions above;
  - the agent decision on recent-play ties;
  - a GOALS §4 note on budget mode.
