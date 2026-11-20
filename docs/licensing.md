# Data licensing record

What Juju relies on, where it comes from, and when it was checked. Re-check a source whenever
its "last updated" date changes.

## The Odds API (the odds vendor)

- **Terms:** https://the-odds-api.com/terms-and-conditions.html. The terms say "Last Updated: 31 August 2026".
- **Retrieved:** 2026-09-30.
- **Clauses relied on, quoted:**
  - Permitted: "Storing our data and retaining it indefinitely".
  - Permitted: "Displaying our data in a UI, website, or mobile app, including for commercial use".
  - Permitted: "Calculating and displaying values you derive from our data".
  - Permitted: "Using our data to train statistical and machine learning models".
  - Prohibited: "Do not resell, repackage, or redistribute our data as a standalone data
    product." "This includes, but is not limited to, offering our data through your own API,
    data feed, downloadable files, or any other format intended to serve as a source of raw
    data for others."
  - Attribution: "Attribution to The Odds API is not required, but is always appreciated."
  - Enforcement: "If we reasonably suspect a violation of these terms, including the resale or
    redistribution of our data as a data service, we reserve the right to revoke your API key
    and block future access."
- **How Juju stays inside the terms:**
  - The backend has no public address. Only the site is public, through Fly's private network
    (`web/app/api/[...path]/route.ts`, which forwards an allowlist of UI endpoints).
  - No endpoint lists prices across players or games, and none exports raw data.
  - Every response is one player's or one team's computed cards (payout, fair value, status).
  - A same-game parlay (M4) is the one response that spans players. It shows each leg's price
    at the parlay's book, exactly as that leg's own card shows it, and nothing more: only the 2
    to 6 legs the person picked, in one game, never the other books' prices. The owner decided
    this on 2026-09-30; it is display in a UI, which the terms permit, not a feed.
    `tests/db/test_parlay.py` holds the parlay to it.
  - The owners' own recaps (`/me`, 2026-10-17) are the other exception, and only for the owner
    signed in. Each owner signs in with their own passcode; the bet pages they open are kept
    under their name, and a game's recap lists the bets they looked up across players, with
    the price each page showed (its book only, never the other books). The history box on a
    player's page lists that player's bets across the owner's earlier games. It is the owner's
    own record, behind their own sign-in, never in a cached response, a share image, an OG
    tag or `/api/status`, and nothing can be downloaded or exported. The backend's `/api/me`
    has its own door in the site (`web/app/api/me/`), never on the public allowlist;
    `tests/db/test_me.py` and `web/lib/publicApi.test.ts` hold it to that.
  - **The live chat (2026-11-20)** is the third exception, and the only place a person's
    question reaches The Odds API (`backend/juju/api/live_chat.py`; the first two read what the
    worker stored). It shows live prices, on demand, for the one thing someone asked about:
    one player's or one team's lines in one game (over and under, each line, each book in
    `BOOKS`), or the 2 to 6 legs of a parlay they named. It is display in a UI, as a card is.
    What keeps it from being a feed:
    - A question must name a player or a team. `GET /api/live-markets` without one is refused
      (400), and the chat asks "Which player?" and lists names, never prices. Nothing returns a
      whole market, a whole game's props or an export.
    - The reply from The Odds API (every player in the market) lives in memory for at most 60
      seconds (`LIVE_CACHE_SECONDS` can shorten it, never lengthen it) and is never returned,
      logged or stored. Only the asked-for player's outcomes are read out of it
      (`postings_for` in `ingest/live_odds.py`).
    - The schedule and the list of markets a game offers carry no prices.
    - Offshore books are never shown, and the best price is never the headline.
    - It reads no archived price, so the live and archived products never mix.
    - The assistant (step 3) gets its numbers from five tools that each name one player or team,
      or a parlay's legs; none lists a market or a board, and a name that matches nobody is an
      error. Its words are checked against what the tools returned for that question.
    - It is behind the same passcode gate as the rest of the site, and is off (404) at the site
      unless `SITE_PASSCODE` is set.
    `tests/db/test_live_chat.py` holds it to these.
  - Rate limits and Turnstile run at Cloudflare, and Juju's terms forbid scraping.
  - The footer credits The Odds API, which is appreciated but not required.

## nflverse (next-day verification, M3)

- The data repository `nflverse/nflverse-data` is licensed **CC-BY-4.0**, as shown by GitHub's
  license tag, retrieved 2026-09-30.
- Commercial use is allowed with attribution, so the site footer credits nflverse.
- Juju reads the release files directly (`juju/ingest/nflverse.py`), not through `nflreadpy`.

## ESPN (live game state, box scores, rosters): open

- ESPN's `site.api` endpoints are unofficial and have no published license for commercial use.
- Juju uses them under parlaytracker's etiquette:
  - an honest User-Agent (`Juju/1.0`);
  - single dates only;
  - at most 1 request every 2 s per host and 20 a minute overall;
  - backing off on 403 and 429.
- **Decide before public launch:** keep ESPN, or buy a licensed stats feed behind the same
  interface (`juju/ingest/router.py`, `worker/live.py`).
