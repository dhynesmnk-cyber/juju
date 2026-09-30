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
