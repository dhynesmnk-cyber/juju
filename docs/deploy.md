# Deploying Juju: Fly.io (iad) behind Cloudflare

Juju runs as two Fly apps and one managed Postgres, with Cloudflare in front of the site.

| Piece | What it is | How it scales |
|---|---|---|
| `juju-web` | Next.js, the only public surface | at least 1 machine, never auto-stopped |
| `juju-backend`, `api` process | FastAPI, private only (no public IP) | 1 machine to start, 2 in season |
| `juju-backend`, `worker` process | the archive and the live loop | **exactly 1**, never stopped (the advisory lock refuses a second) |
| Postgres | Fly Managed Postgres in `iad`, daily backups | |

`juju-web` reaches the API at `api.process.juju-backend.internal:8000`, which resolves to the
`api` process group only.

## One-time setup

The owner does this, or a session that has been given a `FLY_API_TOKEN`.

```bash
fly auth login
fly apps create juju-backend && fly apps create juju-web

# Postgres: create a Managed Postgres cluster in iad in the Fly dashboard, then:
fly secrets set -a juju-backend DATABASE_URL='postgres://...'

# An Odds API key used only by Juju, on the 100K plan. Never paste it into a chat.
fly secrets set -a juju-backend ODDS_API_KEY=...
# Optional: the LLM fallback for free text (OpenRouter's OpenAI-compatible API).
fly secrets set -a juju-backend LLM_API_KEY=... LLM_MODEL=<an OpenRouter model id>

cd backend && fly deploy          # runs `alembic upgrade head` first
fly scale count api=1 worker=1 -a juju-backend
fly ips list -a juju-backend      # must list no public address; release any with `fly ips release`

cd ../web && fly deploy
fly certs add <your domain> -a juju-web
# Then set SITE_URL in web/fly.toml to https://<your domain> and deploy again, so link
# previews (the share images) point at the domain.
```

About a minute after the worker starts, `/health` on the API (`fly ssh console -a
juju-backend`, then `curl localhost:8000/health`) should show `"ok": true`.

## Cloudflare

1. **DNS:** add the domain, and a proxied (orange-cloud) CNAME to `juju-web.fly.dev`.
   Set SSL/TLS to **Full (strict)**.
2. **Cache rule:**
   - Applies to `/api/player/*`, `/api/team/*`, `/api/parlay/*`, `/api/live`, `/api/suggest`
     and the share images, `/g/*/image` (a settled card's image is cached for a day, a live
     one for 30 s).
   - Set "Eligible for cache", with the edge TTL respecting the origin.
   - The backend sends `s-maxage=5` while a game is live and `300` once it is final. A viral
     play then costs the backend about one request every 5 s per player, however many people
     look it up.
3. **Rate-limit rule:** `POST /api/lookup`, 60 requests per 10 s per IP, then **block** for
   a minute. Not a managed challenge: lookups are a `fetch` from the search box, so a
   challenge page would reach it as an error nobody can solve. People are checked in the page
   instead (5). The API also allows at most 30 lookups a minute per person.
4. **Bot Fight Mode:** on. Juju's terms forbid scraping (docs/licensing.md).
5. **Turnstile:** create a widget (managed mode) for the domain, then
   `fly secrets set -a juju-web TURNSTILE_SECRET_KEY=...` and deploy the site with
   `--build-arg NEXT_PUBLIC_TURNSTILE_SITE_KEY=<site key>`. After 12 lookups in 15 minutes,
   the API asks for a check; the search box shows the widget and retries the lookup, and a
   person who passes isn't asked again for an hour (a signed cookie). Without these keys
   nothing is asked, and only the limits in (3) apply. If Cloudflare can't be reached to
   verify a token, the lookup goes through rather than locking people out.

## The worlds' kill switch

The animated worlds (the shader and the Jujus) can be turned off without touching the backend:
`fly deploy -a juju-web --build-arg NEXT_PUBLIC_WORLDS=off`. That is a build-time setting in
Next.js, so it needs a web redeploy. The site then shows no world, and returns to the normal
light and dark theme. Phones already fall back on their own when frames are slow, and so does
Save-Data.

## Alerts

- **Site uptime:** point a monitor (Better Stack, UptimeRobot) at `/about` on the site.
- **Backend health:** `/health` on the backend reports the worker heartbeat, every provider's
  circuit breaker, the Odds API credits left, and the next captures.
- **Missed captures:** the `check_captures` job logs an ERROR for any game past T-44 without an
  on-time price. `repair_gaps` then fills it from the vendor's history.
- **Next-day check:** `verify_games` runs at 10:07 and 16:07 ET (and at startup). It checks
  last week's final games against nflverse, replaces any stat the official numbers correct, and
  logs an ERROR for a game whose final score disagrees (nothing is applied; see
  `games.last_error`). From the play-by-play it confirms the longest plays and the first
  touchdown's scorer, and never corrects them (a disagreement leaves the card unverified; the
  first touchdown's is `games.first_td_official`). Run it by hand with
  `python -m juju.cli verify`. It needs outbound HTTPS to `github.com` and
  `release-assets.githubusercontent.com`.
- **Logs:** ship them with Fly's log shipper, and alert on `ERROR juju.`.

## First real capture

1. With the worker running and a key set, watch `next_captures` in `/health` for the next game.
   The slots `t180`, `t60`, `t49`, `t47` and `t45` should appear one by one.
2. `python -m juju.cli unmapped` should list few Odds API player names, or none.
3. Backfill the season so far. It prints the credit estimate and asks before spending:
   `fly ssh console -a juju-backend -C "python -m juju.cli backfill 2026-09-10 2026-09-29"`.

## Monthly costs (estimates)

| Item | Monthly |
|---|---|
| The Odds API, 100K plan | $59 |
| Fly machines (web, api, worker) | about $10–15 |
| Fly Managed Postgres, smallest | check current Fly pricing |
| Cloudflare free plan | $0 |
| LLM fallback | cents: it is only called when the parser isn't sure, with a daily cap |
