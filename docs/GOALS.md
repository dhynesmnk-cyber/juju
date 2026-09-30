# Juju: goals, v2

**Date:** 2026-09-30
**Status:** The goals are confirmed and have been reviewed for user experience and stability. This file
replaces `GOALS-v1-brainstorm.md`, which is kept unchanged for the record.
**Reference code:** `dhynesmnk-cyber/parlaytracker@c3bd43c`. It is a pattern donor: files are
copied from it with a `Ported from …` header, and parlaytracker itself is never changed.

---

## 1. The product in one sentence

Juju is a public website where you describe a recent NFL play (tap it, pick a player or type one
line) and see what a **$10 bet placed 45 minutes before kickoff (T-45)** would have paid. It
uses real archived prices and never invented ones.

Juju **does not take bets**, hold funds or place wagers, and it does not link to sportsbooks. It is
a tool for looking back at prices.

## 2. Confirmed decisions

| # | Question | Decision |
|---|---|---|
| 1 | What does "expected payout" mean? | **Both**, with the actual result as the headline (§5). |
| 2 | Where do T-45 odds come from? | **Real prices we archive ourselves**, captured before kickoff. Nothing is invented. Assume the user is looking a play up within 30 seconds of it happening live. |
| 3 | Where does it live? | **A new project called Juju, with its own backend.** |
| 4 | How does the user input the play? | **Free text + LLM parse + confirmation card**, refined on 2026-09-30: tappable recent plays and player typeahead sit alongside the text box, and the confirmation card appears **only when the match is uncertain**. |
| 5 | What can Juju price? | **Full scope:** NFL player props, game and team lines, and same-game parlays. |
| 6 | Book of record (2026-09-30) | **Hard Rock Bet**, then DraftKings, then the others (`BOOKS` setting). |
| 7 | Hosting (2026-09-30) | **Fly.io** (`iad`) for the site, API, worker and Postgres, with **Cloudflare** in front. |
| 8 | Odds vendor | **The Odds API**. Its terms allow Juju's use (§3). |
| 9 | The look (2026-09-30) | **Worlds that follow the bet's state**, drawn by one WebGL shader (option A, 7 KB gzipped). Three original characters, the **Jujus** (Pip, Bo and Tuft), sit on the first card and react. The price always renders first; see §7.9. |

## 3. Licensing: a design rule, not a blocker

The Odds API terms (https://the-odds-api.com/terms-and-conditions.html, last updated
31 Aug 2026; the clauses are quoted in [licensing.md](licensing.md)):

- **Allow** storing the data indefinitely, displaying it in a website or app including for commercial
  use, calculating and displaying derived values, and training models.
- **Forbid** reselling or redistributing it as a standalone data product. That includes offering it
  through our own API, a data feed or downloadable files meant as a source of raw data for others.

Juju sells the software layer, never the feed. These rules are built into the architecture:

1. The FastAPI backend has **no public address**. Only the Next.js site is public, and it reaches
   the backend over Fly's private network.
2. There are no list, bulk or export endpoints and no odds boards. A response is the computed cards
   for **one** player or team, or a UI list with no prices in it.
3. Rate limits and Turnstile run at Cloudflare, and Juju's own terms forbid scraping.
4. The historical endpoints fall under the same terms, so they are used for gap repair and backfill.

The only open licensing questions are ESPN (an unofficial API with no license, §6.3) and
attribution. The nflverse data is CC-BY-4.0 (not MIT, as v1 said), so the footer credits it.

## 4. The archive (stability)

- **Capture around T-45, not all day.** A snapshot taken after T-45 is never used.
  - At T-3 h: main markets, as a coverage check.
  - At T-60, T-49, T-47 and T-45:30: every market.
  - A failed capture retries every 20 s until T-45:00. Nothing is captured after that.
- **Capture wide, display narrow.** Up to 10 books cost the same as one region, so one request asks
  for all of `BOOKS` and for the main and alternate player markets. Fetching the same data later
  from the historical endpoint costs 10×.
- **Repair automatically.** From T-44, a game with no on-time snapshot is filled from the historical
  endpoint. It returns "the closest snapshot equal to or earlier than" the requested time, which is
  our own rule. It retries hourly, within a budget. The same code backfills the season so far.
- **Pick the snapshot when a card is read.** T-45 is always worked out from the game's current
  kickoff time, so a moved kickoff re-selects on its own. Prices are never interpolated.
- **Pick the price** (`core/t45.py`):
  - The target is kickoff − 45:00.
  - For each book, take its latest snapshot at or before the target.
  - Walk the book chain and use the first price that is **on time** (≤ 5 min early). Otherwise
    use the first that is **early** (≤ 3 h, flagged with its real time). Otherwise the result is
    **no price**, with the reason.
- **Provenance:** every price keeps its snapshot, the fetch time, the vendor's market
  `last_update`, and the sha256 of the gzipped raw payload.
- **Budget:** about 8k credits a month for the archive, 5–9k for live scores and about 2k for
  repairs. That is the 100K plan ($59/mo), on a key used only by Juju.

## 5. The result card

Two independent parts, plus notes:

- **Price:**
  - `ON_TIME`
  - `EARLY`, flagged with its real time
  - `NONE`, with the reason
- **Outcome:**

  | Outcome | What it means |
  |---|---|
  | `PREGAME` | The game hasn't started. |
  | `WAITING_FOR_FEED` | The user's play isn't in the feed yet. The card polls. |
  | `LIVE` | Shows the current stat, the line and what's still needed. |
  | `LOCKED` | The line is already beaten. "Paid if the play stands." |
  | `GONE` | Already lost before the final whistle (for example, someone else scored first). |
  | `WON`, `LOST`, `PUSH` | Only after the game has been final for 10 minutes. |
  | `NO_STAT_LINE` | The game is final and the player never appeared in the box score. Never assumed to be zero, never assumed to be void. |
  | `UNTRACKED` | Juju can't follow this stat live. The price is still shown. |
  | `UNAVAILABLE` | The live feed is down. The price is still shown. |

- **Notes:** `AWAITING_VERIFICATION`, `CORRECTED` (a stat changed after settling), `SGP_ESTIMATE`.

Rules:

- **Fair value** is shown only when both sides were captured in the same snapshot. The TD-scorer
  markets are "Yes" only, so their card says the book offered one side.
- **A player who appears anywhere in the box score has played,** so a stat he's missing is a real 0.
  A player who appears nowhere has "no stat line yet".
- **Same-game parlays** (M4) use standard parlay maths, and every one carries: *"Standard parlay
  maths. Books adjust same-game parlays for correlation, so a real ticket would have paid less."*

## 6. Live data (stability)

1. **Requests from users never reach a data provider.** The API reads Postgres and in-process
   caches, and only the worker talks to ESPN or The Odds API. The optional LLM fallback is the
   one exception.
2. **Show the price first and let the status catch up.** ESPN runs 10–30 s behind TV, so a lookup
   30 s after a play usually arrives before the play is in the feed. The price shows immediately,
   and the status updates every 10 s.
3. **ESPN is fragile:**
   - it is unofficial and has blocked cloud IPs;
   - parlaytracker's etiquette limits it to 20 requests a minute.

   So:
   - The Odds API's `/scores` is an independent source for game state and scores.
   - Box scores are read in priority order: games whose score just changed, then games people are
     looking up, then round-robin.
   - A `LiveStatsSource` interface makes room for a licensed feed. That decision is needed
     **before public launch**.
4. **parlaytracker's safety code is reused:**
   - circuit breakers and failure kinds, and host failover;
   - integrity guards (a stale reading is discarded, a final never reverts, frozen-feed probe,
     plausibility bounds);
   - freshness limits (amber at 2 min, red at 5).

## 7. User experience

1. **Home:** player typeahead (live games first) and **Live now**, each live game's latest scoring
   plays and 20+ yard gains as tap targets. When nothing is live, it shows the next kickoff and
   recent games.
2. **A play shows every bet it touched.** Cards appear in this order: cashed by this play, then
   still live, then the rest. A market or threshold the user names ("100+ rush yds") comes first.
3. **Resolve in context.**
   - Candidates come from live games first, then recent games, then the league.
   - A score of 90 or more is accepted automatically. 75–89 is doubtful, and so is a runner-up
     within 3 points.
   - A doubtful match gets a one-tap confirmation card.
4. **The LLM is a fallback.** It is only called when the deterministic parse isn't sure, has a 3 s
   timeout, and never supplies a displayed number.
5. **Every card names its book** and lists the other books' prices. The headline never shows the
   best price, and offshore books are never shown.
6. **Permalinks:** `/g/{game}/{player}` keeps updating until the game settles. Cloudflare caches
   it for 5 s while live.
7. **Mobile-first and accessible.**
   - A status is always text plus an icon.
   - WCAG AA contrast.
   - Tap targets are at least 44 px.
   - Local time is shown, with ET beside it.
8. **Compliance without nagging.**
   - A one-tap 21+ confirmation, remembered on the device.
   - "Hypothetical. Juju is not a sportsbook." on each card.
   - The footer carries 1-800-GAMBLER and the data credits.
   - No sportsbook links, no "bet now" and no recommendations.
9. **Worlds and the Jujus.**
   - The page's world follows the first card:
     - pregame: a night sky and a moon;
     - live: tower lights and camera flashes;
     - waiting: a searchlight;
     - cashed: a gold shockwave from the card, confetti and a count-up;
     - won: dawn;
     - lost: grey dusk and rain;
     - feed down: static.
   - The Jujus doze, cheer, keep watch with binoculars, jump, hold a trophy, shelter under an
     umbrella, or fix the antenna.
   - Guardrails:
     - The price is server-rendered, and the shader starts only when the browser is idle.
     - The count-up ends exactly on the API's number.
     - The world steps down its resolution, then falls back to CSS, when frames are slow.
     - It pauses when the tab is hidden, and honours Save-Data.
     - Reduced motion gives colour fades only, with the final number shown at once.
     - The home page never celebrates.
     - The hypothetical line stays visible through every celebration.
     - The kill switch is `NEXT_PUBLIC_WORLDS=off`.

## 8. Still binding from parlaytracker

- Never print, log or commit API keys. Secrets are `SecretStr`.
- No test calls a real external service. Tests use recorded fixtures, and a socket guard enforces this.
- No scraping. ESPN is used under parlaytracker's rules: an honest User-Agent, single dates,
  its rate limits, and backing off when blocked.
- Failures are visible, never silent. The API has a health report and the site shows a banner.

## 9. Corrections to v1

- **Result states:** v1 said four but listed five. PUSH and VOID or no stat line were missing (§5).
- **Market counts:**
  - parlaytracker has 12 market types, not 8.
  - The Odds API lists about 30 NFL player markets, including "Yes"-only TD scorers and longest
    rush and reception.
- **Licences:** the nflverse data is CC-BY-4.0.
- **The Odds API:**
  - It also has a 5M plan at $119/mo.
  - Historical data is available on paid plans only.
  - In event odds, `last_update` exists per market only.
  - Caesars and Fanatics come back only on paid plans.
- **parlaytracker's GitHub repository is public.** Check that this is intended.

## 10. Milestones

| | Scope | Status |
|---|---|---|
| M0 | Docs, backend skeleton, ported modules and tests, CI | done |
| M1 | Archive (capture, repair, backfill), lookup engine, internal API | done |
| M2 | Live overlay, deterministic parse and LLM fallback, Next.js UI, deploy config | done |
| M3 | Worlds and the Jujus, nflverse verification and corrections, the play that decided it, lookup golden set, share images, Turnstile past a lookup threshold | done |
| M4 | Same-game parlays | next |
| M5 | Launch hardening: licensed live stats, load test, alerting, restore drill, legal review of state exposure | before launch |

## 11. Open before public launch (not before the build)

1. A Juju-only Odds API key on the 100K plan.
2. The live-stats source: keep ESPN, which is unlicensed, or buy a licensed feed.
3. The legal view on showing prices in restricted states (v1 §11).
4. A domain on Cloudflare, and the Fly.io apps (see [deploy.md](deploy.md)).
