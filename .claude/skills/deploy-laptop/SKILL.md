---
name: deploy-laptop
description: Deploy, update or fix Juju's private preview on the owner's own Ubuntu machine (Docker Compose for Postgres, API, worker and site; Tailscale Funnel; a shared passcode; the recorded demo game), all driven by deploy/laptop/up.sh. Use it whenever the owner wants Juju online from their laptop or a home machine, wants to update, restart, take offline, back up or change the passcode of that copy, wants to add an Odds API key to it, or pastes output from up.sh, docker compose or tailscale funnel, even if they only say "the site is down", "deploy it" or "my users can't get in". Not for the Fly.io launch setup (docs/deploy.md).
---

# Juju on the owner's machine

The owner chose this on 2026-10-07 for a private preview: their old Ubuntu laptop, $0 a month,
for themselves and two users, with the recorded PHI @ CHI game as demo data. Fly.io
(`docs/deploy.md`) stays the plan for launch.

**You can't reach their machine.** The owner runs `deploy/laptop/up.sh` and pastes output back.
Don't ask for SSH access, a shell, or any secret in the chat. Your job is to keep the script,
the Compose file and this skill right, and to read what they paste.

## How it fits together, and why

| Piece | Where | Why it is like this |
|---|---|---|
| `db`, `migrate`, `api`, `worker`, `web` | `deploy/laptop/docker-compose.yml` | The Fly layout on one machine. `migrate` runs once before `api` and `worker`, as Fly's `release_command` does. |
| Only `web` has a port, `127.0.0.1:3000` | compose `ports` | Funnel publishes that. The API and Postgres have **no published port**: "the backend has no public address" is a licensing rule (`docs/licensing.md`). `up.sh` refuses to finish if either has one. |
| The passcode gate | `web/proxy.ts`, `web/lib/passcode.ts`, `web/lib/unlockPage.ts`, `web/app/unlock/check/route.ts` | Off unless `SITE_PASSCODE` is set, so development, CI and Fly are unchanged. A locked page gets a self-contained HTML form (401) **at the address asked for**; an API call gets a JSON 401. The cookie lasts 30 days and is signed with a key derived from the passcode, so changing it signs everyone out. Only the build's static files skip it: `/_next/image` is gated, because it fetches pages itself and could hand out share images. |
| Settings | `deploy/laptop/.env` (mode 600, gitignored) | `POSTGRES_PASSWORD`, `SITE_PASSCODE`, `SITE_URL`, and later `JUJU_ODDS_API_KEY`. `up.sh` creates the first three. |
| Compose runs under `env -i` | `compose()` in `up.sh` | Compose takes a variable from the shell before `.env`. On 2026-10-07, testing in a cloud container, the worker picked up parlaytracker's `ODDS_API_KEY` from the shell and started with "archive on"; it made only the free events call. Hence the clean environment, the `JUJU_` name, and the check that stops the worker if it archives without that key. |
| The demo game | `up.sh`, step "The recorded demo game" | `python -m juju.cli seed final`, once (looked up by ESPN event `401872963`). The production image doesn't ship `backend/tests/`, so it is bind-mounted read-only for that one run. `final` keeps the real timeline: prices captured about two hours before kickoff, flagged EARLY. Nothing is invented. |
| Funnel | `sudo tailscale funnel --https=443 --bg http://127.0.0.1:3000`; off: the same target with `off` | A stable `https://<machine>.<tailnet>.ts.net` with no domain or router changes. Always sudo, with output visible: when Funnel isn't allowed yet, Tailscale prints a link and waits. The tailnet ID isn't needed and doesn't belong in the repo. |
| Fails closed | `unsafe()` in `up.sh` | Funnel stays on between runs. If the gate checks or the port check fail on a rerun, the site would be online and open, so `up.sh` turns Funnel off before it stops (or, if it can't, says so in capitals). |

Why the gate never redirects: a redirect from `proxy.ts` needs an absolute URL, and behind a
tunnel the request's own host can be wrong (a relative `Location` from the proxy is a 500 in
Next.js 16). Rendering the real `/unlock` page through a rewrite was tried too: the app shell's
link prefetches got rewritten as well and the page kept reloading. A plain HTML form with no app
code has neither problem. The form posts to `/unlock/check`, a route handler, which may redirect
relatively.

## What `up.sh` does

1. **Checks the machine:** Docker and Compose v2 usable without sudo, curl, openssl, python3,
   memory (a warning under ~2 GB) and disk (stops under 5 GB). With Funnel: Tailscale installed
   and logged in, and MagicDNS giving a name.
2. **Writes `.env`** if values are missing: a random database password, the passcode (typed,
   or made up as `xxxx-xxxx-xxxx`) and `SITE_URL` from the Tailscale name. Reruns keep them.
3. **Builds, migrates, seeds the demo once, starts** `api`, `worker` and `web` with `--wait`.
4. **Checks, failing loudly before anything goes online:**
   - an API call without the passcode is a 401, and a page shows the form;
   - the passcode from `.env` opens it;
   - the site reaches the API, and the demo player page renders;
   - no published port on `api` or `db`;
   - the worker says "archive off" unless `JUJU_ODDS_API_KEY` is set.
5. **Funnel**, with sudo if needed. Then it prints the address, the passcode only if it just made
   one up, and direct links to the demo game, which the home page never lists (it shows the last
   14 hours to the next 36).

`./up.sh --no-funnel` does steps 1 to 4 and leaves the site on `http://127.0.0.1:3000`.

## When the owner pastes a failure

Read the last `== step` and the `Stopped:` line. Most failures are one of these:

| Symptom | Cause and fix |
|---|---|
| `Docker isn't installed` / `permission denied ... docker.sock` | Install `docker.io docker-compose-v2`; `sudo usermod -aG docker $USER`, then **log out and in** (a new terminal isn't enough). |
| `429 Too Many Requests` from `registry-1.docker.io` | Docker Hub's anonymous pull limit. Wait an hour, or `docker login` with a free account. |
| The build ends `Killed` or exit 137 | Out of memory building the site. Add swap (`sudo fallocate -l 2G /swapfile`, then `mkswap`, `swapon`). |
| `something else is already using port 3000` | Another dev server. Stop it, or change the published port in the compose file and the Funnel target in `up.sh` together. |
| `container juju-web-1 is unhealthy` | `docker compose ... logs web`. The health check accepts 200 or 401 from `/about`; anything else means the server didn't start. |
| `the passcode gate is off` | `SITE_PASSCODE` didn't reach the container. **Nothing is online yet**: keep it that way until the check passes. |
| `Tailscale isn't logged in ... or MagicDNS is off` | `sudo tailscale up`; turn on MagicDNS and HTTPS certificates in the admin console's DNS page. |
| `Funnel didn't start` | Funnel isn't allowed for the machine. Open the link it printed, or add the `funnel` node attribute in the tailnet policy. |
| The URL doesn't answer right after Funnel starts | The first certificate takes up to a minute; try a browser. |
| `the worker started archiving without JUJU_ODDS_API_KEY` | A key reached it from somewhere other than `.env`. It has been stopped; find out how before starting it again. Never parlaytracker's key. |
| Users get the form again and again | Their browser blocks cookies for the site, or the passcode changed (everyone re-enters it). |

To see more, ask for `docker compose -f deploy/laptop/docker-compose.yml logs --tail 100 <service>`.
Never ask for `.env`.

## Changing it

- Keep the invariants above: only `web` published, the gate on, one worker, no stray key.
  `up.sh`'s checks encode them; extend the checks when you add something.
- Shell: `bash -n deploy/laptop/up.sh` and shellcheck (no local binary:
  `docker run --rm -v "$PWD/deploy/laptop:/mnt:ro" koalaman/shellcheck:stable /mnt/up.sh`).
- The gate: `cd web && npm test` (`lib/passcode.test.ts`), and the full web checks in CLAUDE.md.
  e2e runs without a passcode, so it can't catch a broken gate: `up.sh` and a browser walk can.
- Then deploy it for real in a cloud container (below) before telling the owner to pull.

## Testing it in a Claude Code cloud container

Docker works there, with three workarounds that are only for that environment. Never put them in
the repo's Dockerfiles: the owner's machine doesn't need them.

1. Start the daemon:
   `nohup env HTTPS_PROXY="$HTTPS_PROXY" HTTP_PROXY="$HTTPS_PROXY" NO_PROXY=localhost,127.0.0.1 dockerd > dockerd.log 2>&1 &`
2. Docker Hub answers 429 to the shared address. Pull `mirror.gcr.io/library/{postgres:16,python:3.12-slim,node:22-slim}`
   and tag each under its usual name.
3. Outbound TLS is intercepted. Rebuild the two base images, under the same tags, to trust
   `/root/.ccr/ca-bundle.crt`:
   - Python: set `PIP_CERT`, `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to the bundle.
   - Node: copy it to `/etc/ssl/certs/ca-certificates.crt` and set `SSL_CERT_FILE` and
     `NODE_EXTRA_CA_CERTS`. Turbopack fetches Google Fonts from Rust, which ignores Node's
     setting.

Then run `deploy/laptop/up.sh --no-funnel < /dev/null` (a passcode is made up). Walk it in
Playwright at phone width: the form, a wrong passcode, the right one, the demo pages. Read the
passcode from `.env` without printing it. Afterwards: `docker compose -f
deploy/laptop/docker-compose.yml down -v` and delete `deploy/laptop/.env`.

## Moving to Fly later

`docs/deploy.md` is the launch runbook. The gate works there too: set `SITE_PASSCODE` as a
`juju-web` secret, and accept a 401 in its `/about` health check, or Fly marks the site unhealthy.
To keep the preview's data, `pg_dump` from the `db` container and restore into Fly Postgres
before the first `fly deploy`.
