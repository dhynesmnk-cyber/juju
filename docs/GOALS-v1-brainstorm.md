# Juju — Goals, confirmed

**Date:** 2026-09-30
**Status:** Goals confirmed with the product owner. Not yet a build plan.
**Reference codebase:** `dhynesmnk-cyber/parlaytracker` (cloned to `/home/user/parlaytracker`) — treated as a *pattern donor*, not a codebase to modify.

---

## 1. The product in one sentence

Juju is a public website where a user types one line describing a recent NFL play and gets back
what a **$10 bet placed at T-45 minutes before kickoff** would have paid — using real archived
prices, never invented ones.

Juju **does not take bets**, does not hold funds, does not place wagers, and does not link to
sportsbook deposit flows. It is a look-back pricing tool.

---

## 2. The five confirmed decisions

| # | Question | Decision |
|---|---|---|
| 1 | What does "expected payout" mean? | **Both**, actual result as the headline. Owner chose "advise me"; see §3. |
| 2 | Where do T-45 odds come from? | **Self-archived real prices**, captured by our own poller before kickoff. Owner directive: *"remove any constraints from previous spec or MD that stop from reaching goals. ensure all data is not invented. assume user is looking it up within 30 seconds of it happening live."* |
| 3 | Where does it live? | **New project with a separate backend, called Juju.** Not a page in `parlaytracker`. |
| 4 | How does the user input the play? | **Free text + LLM parse + confirmation card.** |
| 5 | What can Juju price? | **Full scope:** NFL player props, game/team lines, *and* same-game parlays. |

---

## 3. Decision 1 resolved: what the result card shows

The owner asked for a recommendation. **Recommendation: show both, actual result first.**

Rationale:

- "What would this have paid?" is the question a fan actually has, and the answer is
  deterministic and *checkable*. A wrong number gets caught; that accountability is what makes a
  public pricing site credible.
- A bare payout number teaches nothing about price. $10 at −350 returning $12.86 *feels* like a
  loss even though it won. So every card also carries the de-vigged fair value, which shows the
  user what the vig cost them.
- `parlaytracker/core/odds.py` already implements `decimal_odds()`, `american_odds()`,
  `payout()`, `implied_probability()`, `no_vig()` and `parlay_decimal()`. This is nearly free
  to port and it is already tested.

**Result card states** (this is the important part — there are four, not one):

| State | When | What Juju shows |
|---|---|---|
| `WON — settled` | Game final, over hit | `$10 → $19.09 returned (+$9.09)` at the archived price, plus the play that decided it |
| `LOST — settled` | Game final, over missed | `$10 → $0` plus the final stat vs the line |
| `CASHED — locked in` | Game live, stat **already exceeds** the line | `$10 → $X guaranteed if it stands` — mathematically certain, not a prediction |
| `STILL LIVE` | Game live, line not yet beaten | Current stat, what's still needed, and the payout **if** it hits. Never presented as a probability we made up. |
| `NO PRICE ON FILE` | Play predates the archive | Say so plainly. **Never fabricate a number.** |

Note the `CASHED — locked in` state: because the owner expects lookups ~30 s after a live play,
most props will *not* have resolved yet. A 60-yard TD catch does not settle "Over 100.5 receiving
yards". The honest answer is either "already guaranteed" or "still live", and Juju must not
pretend otherwise.

---

## 4. Constraints lifted from `parlaytracker`

The owner directed that anything in the old SPEC/MD blocking this goal be removed. Recorded here
so the waiver is explicit and auditable. **These waivers apply to Juju only; `parlaytracker`
itself is unchanged and still private.**

| `parlaytracker` rule | Status for Juju |
|---|---|
| SPEC §1.3 — "Historical odds backfill" out of scope | **Waived.** Juju needs archived pregame prices. |
| SPEC §1.3 — "No JavaScript frontend" | **Waived.** Public consumer site needs a real frontend. |
| SPEC §1.3 — "No separate API server" | **Waived.** Juju has its own backend. |
| SPEC §1.1 — "Streamlit only" | **Waived.** |
| SPEC §1.1 — "Free sources only" | **Waived.** Paid odds credits are expected (§8). |
| SPEC §1.1 — "Qwen models only, no Anthropic" | **Waived** if a different model serves the parse better. |
| SPEC §1.1 / §12 — home laptop behind Tailscale, never public | **Waived.** Juju is public and needs real hosting. |
| SPEC §1.1 — "Two users, Tailscale login" | **Waived.** Juju is anonymous/public. |
| CLAUDE.md — "Deployment is a home laptop... never exposed publicly" | **Waived for Juju.** |

**Still binding on Juju** (these are good rules, not blockers):

- Never print, log or commit API keys. Secrets stay typed as secrets.
- No test calls a real external service; use recorded fixtures.
- No scraping.
- Failure states are visible, never silent. This matters *more* for Juju, because a silent
  failure becomes a confidently wrong payout number shown to the public.

---

## 5. What Juju takes from `parlaytracker`

Reuse the proven parts rather than rewriting them:

| Asset | Why it matters to Juju |
|---|---|
| `core/odds.py` | American↔decimal, payout, implied probability, `no_vig`, `parlay_decimal`, Wilson interval. Pure, tested, uses `Decimal` for money. |
| `core/settlement.py` | Win/loss/push rules per market, including the `alt_spread` margin maths. |
| `ingest/http.py` | Shared httpx client, `RateLimiter`, `FetchError` with a `FailureKind` per failure. |
| `ingest/router.py` | Per-provider circuit breakers + ESPN host failover (`site.web.api` → `site.api`). |
| `ingest/resolve.py` | Team alias tables, market alias tables, rapidfuzz player matching (threshold ≥ 90, ignores Jr./III). **This is exactly what the free-text parse needs to snap a name onto a real roster ID.** |
| `ingest/espn.py` | Box-score parser (`parse_box_score`), scoreboard + roster parsers, status mapping, US-Eastern game days. |
| `ingest/odds_api.py` | Odds API client + Pydantic parsers, reads `x-requests-remaining` / `x-requests-last`. |
| `ingest/extraction.py` | The Qwen-via-OpenRouter structured-output pattern: one call, `temperature=0`, `json_schema`, forgiving `parse_reply` (handles code fences, Unicode minus, string numbers). **Directly reusable for parsing the one-line play.** |
| `core/markets.py` | The 8 NFL player-prop market types and which sports support them. |
| `tests/fixtures/` | Real recorded ESPN, Odds API and nflverse responses — including `nfl_events_2026-09-28.json` and `nfl_event_odds_2026-09-28_PHI-CHI.json` with all 14 NFL market keys. These cost credits; reuse them. |

**Gap found:** `parlaytracker/ingest/nflverse.py` loads only `load_schedules`,
`load_player_stats` and snap counts. It does **not** load `load_pbp()`. Juju needs play-by-play
for play-level granularity — and nflverse's pbp archive is **MIT licensed**, which makes it the
one genuinely free-and-commercially-usable play-level source in the stack. Adding it is a new
module, not a change to the old one.

---

## 6. Blocker: redistribution licensing (must resolve before public launch)

This is the single biggest risk to the project and it is legal, not technical.

Every candidate odds vendor restricts public display of their data:

- **The Odds API** — Terms and Conditions: *"Do not resell, repackage, or redistribute our data
  as a standalone data product."*
- **SportsGameOdds** — Terms: *"No Resale or Redistribution. You will not: sell, resell,
  sublicense, scrape, lease, rent, loan, or distribute..."*
- **ParlayAPI** — homepage, explicitly: *"Paying for a self-serve plan does not by itself grant
  redistribution rights or a custom license."* Their paid tiers require contacting them for
  "public display, commercial use".

Juju is precisely a public website displaying book prices. **A standard self-serve plan almost
certainly does not cover it.**

Actions required:
1. Contact the chosen vendor(s) and get **written** confirmation of public-display /
   redistribution rights before launch. Budget for a commercial license line item.
2. Keep a **provenance record** for every displayed number: source, book, exact snapshot
   timestamp, raw payload hash. This is required for auditability and it is the evidence that
   nothing was invented.
3. Consider whether showing a **derived payout** ("$10 would have returned $19.09") rather than a
   raw odds board is a materially different use. Get an answer from the vendor, not from us.
4. Free and commercially usable, so no license problem: **nflverse** (MIT) for play-by-play,
   player stats, schedules. **ESPN's public API** is used by `parlaytracker` today but its terms
   should also be checked for a public product.

---

## 7. Blocker: no vendor archive reliably holds T-45 player-prop prices

- **ParlayAPI's historical archive is not what it first appears.** Their own coverage page lists
  `americanfootball_nfl` at **19,695 rows, 9 sources, 1999-09-12 → 2026-02-08**, sourced from
  `sportsbookreviewsonline` "legacy NFL **opening + closing** lines through 2026-02-08 (ended at
  Super Bowl)" and `draftkings_espn` "DraftKings **closing-line moneylines**". That is game
  lines and closing moneylines — **not** player props, and **not** a T-45 snapshot. Their
  `/v1/historical/.../odds` endpoint is a TOA-style pull over that archive, so it inherits the
  same limits. Their forward `line-movement` capture is real but starts when you subscribe.
- **The Odds API** does hold genuine historical player props from **2023-05-03** at roughly
  5-minute snapshots, but at **10× the quota cost** per region per market. This is the credible
  backfill path for games before our own archive exists — if licensed.

**Conclusion:** the owner's 30-second-live-lookup assumption makes self-capture the *primary*
source and the correct one. We snapshot the pregame board ourselves from ~T-24 h through kickoff,
so the T-45 price is already in Postgres before anyone asks. Vendor historical endpoints become
an optional, separately-licensed backfill.

**Lookup rule (must be deterministic and honest):** take the archived snapshot with the greatest
timestamp ≤ T-45:00, and *display its real timestamp*. If the nearest snapshot is more than
±5 minutes off T-45, flag it on the card. Never interpolate between snapshots — interpolation is
invention.

---

## 8. Cost reality

The Odds API charges `markets × regions` per snapshot. Full scope (§2, decision 5) is expensive:

| Component | Markets | Rough credits |
|---|---|---|
| NFL player props | 8 (+ alternates) | 8–16 per snapshot per region |
| Game/team lines | `spreads`, `totals`, `team_totals` (+ alternates) | 3–6 |
| Snapshot cadence, pregame | ~14 snapshots/game (T-24 h → kickoff) | ×14 |
| Games per week | ~16 | ×16 |

Order of magnitude: **~50k credits/month for props alone**, and full scope pushes well past
that. Reference tiers found while researching: The Odds API from $30/mo (20k credits),
$59/mo (100k), $249/mo (15M). ParlayAPI: free 1k/mo, $5/mo 20k, $20/mo 100k, $40/mo 1M.
The 500 credits/month free tier in `parlaytracker/.env.example` covers a fraction of one game.

Levers, in order of effectiveness:
1. **One region** (`us`) — already the plan.
2. **One book of record** (e.g. DraftKings or FanDuel) rather than a median across books. Halves
   nothing on cost, but removes the cross-book reconciliation problem.
3. **Adaptive cadence** — sparse snapshots T-24 h→T-2 h, dense (every 5 min) T-90 m→kickoff. The
   T-45 price only needs one good reading in a ±5 min window.
4. **Skip the alternate-line markets** at launch; they roughly double cost and are rarely what a
   viral play refers to.
5. **Cache aggressively.** A pregame board changes slowly; do not re-pull on every tick.

---

## 9. Latency budget for the 30-second window

The owner assumes lookup within ~30 s of the play happening live. Honest end-to-end budget:

| Step | Time | Note |
|---|---|---|
| Play happens → user opens Juju and types | 10–20 s | **The dominant cost, and it is human.** Worth saying out loud: the 30 s target is mostly the user's own reaction and typing time, which sets the real budget for everything else. |
| LLM parse of the one line | 2–4 s | One call, `temperature=0`, structured output. `parlaytracker/ingest/extraction.py` is the template. |
| Snap player/market onto real IDs | <100 ms | Local: rapidfuzz + roster cache, no network. |
| Archived T-45 price lookup | <50 ms | Our own Postgres. No vendor call in the hot path. |
| Live stat / current game state | <50 ms | Served from our worker's cache, **not** a live ESPN call. `parlaytracker/worker/live.py` already polls NFL every 30 s. |

Total machine time ≈ **3 s**. Comfortably inside the window.

**Two honest caveats to design for:**

1. **Feed lag.** ESPN's play-by-play typically lags the broadcast by ~10–30 s. A play the user
   just watched may not be in our feed yet. Juju needs an explicit "we don't see that play yet —
   refresh in a moment" state, and must never fill the gap with a guess.
2. **Stat correction.** Box scores get corrected. `parlaytracker` already treats this as a
   first-class problem (its `verify_nfl` job cross-checks ESPN against nflverse next-day). Juju
   should inherit that: a settled payout can be re-opened and re-labelled if the official stat
   changes.

---

## 10. SGP honesty

Decision 5 includes same-game parlays. `parlay_decimal()` multiplies leg decimal odds, which is
**standard parlay maths** — but books apply correlation adjustments to SGPs, so a real book's SGP
price is usually *worse* than the straight product. Juju's computed SGP payout will therefore be
**higher than what a book would actually have paid**.

Every SGP result must carry that label: *"Standard parlay maths. Books adjust same-game parlays
for correlation, so a real ticket would have paid less."* Omitting it would be a form of
invention, which the owner has explicitly ruled out.

---

## 11. Compliance surface (public + gambling-adjacent)

Juju takes no bets, but it is a public site that prices wagers. Minimum bar before launch:

- **No wagering functionality.** No stake entry tied to an account, no balance, no cashout, no
  affiliate or deep-links into sportsbook signup.
- **Plain "not a sportsbook" statement** and a per-result disclaimer that payouts are
  hypothetical.
- **Age gate** (21+ in most US states) and **geo-consideration** if any jurisdiction is targeted.
- **Problem-gambling resource** (US: 1-800-GAMBLER) in the footer.
- **Data attribution** as the vendor license requires.
- **No bet recommendations.** `parlaytracker` SPEC §1.3 already bans "bet recommendations or
  suggestions" — keep that ban. Juju prices the past; it must not tell anyone what to bet.
- Decide whether showing prices at all creates a regulatory exposure in restricted states.
  This is a legal question, not an engineering one.

---

## 12. Open items — not yet decided

These were not among the five questions and still need answers before or during the build:

1. **Repo location** — new standalone repo `juju`, or a directory inside the existing one?
   (Recommendation: standalone. Separate backend was specified, and it keeps the private app
   clean.)
2. **Stack** — Python/FastAPI + Postgres would let us port `core/odds.py`, `resolve.py`,
   `settlement.py` and the ingest layer almost unchanged. A TypeScript backend would mean
   rewriting all of it. Frontend is open (Next.js/React is the obvious choice for a public
   consumer site).
3. **Hosting** — the current laptop/Tailscale setup cannot serve the public. Needs real cloud
   hosting plus a persistent worker for the archiver.
4. **Odds vendor + license** — which vendor, and written confirmation of public-display rights.
   Blocks launch, not the build.
5. **Which book is the "book of record"** for displayed prices, and whether to show a consensus.
6. **NFL only at launch?** Decision 5 was about market types. `parlaytracker` also handles NBA,
   NHL and MLB; Juju's live archive is NFL-only in the old design.
7. **LLM provider** — Qwen via OpenRouter is already wired and costs fractions of a cent per
   call, but the "Qwen only" constraint is now waived.
8. **Abuse/cost control** — an anonymous public endpoint that triggers an LLM call per request
   needs rate limiting and a budget cap.

---

## 13. Suggested first milestone

A vertical slice that proves the concept end to end on one real game:

1. Stand up the Juju backend with Postgres and the odds-archive table (game, market, player,
   line, book, american odds, snapshot timestamp, raw payload reference).
2. Port `core/odds.py` and `ingest/resolve.py` unchanged, with their tests.
3. Archiver job: snapshot the pregame board for one upcoming NFL game from T-90 m to kickoff at
   5-minute intervals. Confirm a real reading exists at ≤ T-45.
4. Add `load_pbp()` to the nflverse ingest for play-level facts.
5. One-line input → LLM parse → confirmation card → result card, for a single market
   (`player_receiving_yards`) on that one game.
6. Prove the four result-card states, including `NO PRICE ON FILE`, on recorded fixtures.

Nothing in that slice needs a vendor license, because nothing is public yet.
