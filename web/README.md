# juju-web

The public Next.js site. It talks to the backend only through `app/api/[...path]/route.ts`,
which forwards an allowlist of UI endpoints (docs/licensing.md).

```bash
npm ci
npm run lint && npm run typecheck && npm test && npm run build

# End-to-end, at phone width, against the recorded PHI @ CHI game:
cd ../backend && DATABASE_URL=<dev db> .venv/bin/python -m juju.cli seed live
DATABASE_URL=<dev db> .venv/bin/uvicorn juju.api.app:app --port 8000 &
cd ../web && BACKEND_URL=http://127.0.0.1:8000 npx next start -p 3000 &
npx playwright test
```

`e2e/me.spec.ts` (the owners' sign-in, saved lookups and recaps) runs only when the API, the
site and Playwright all have the same test-only `TRACKER_USERS`, as CI does:
`TRACKER_USERS=chris:ci-chris-passcode,ash:ci-ash-passcode` before `uvicorn`, `next start` and
`npx playwright test`. Its lookups stay in the database between runs; it never counts them.

`e2e/challenge.spec.ts` (the Turnstile check) runs only with `TURNSTILE_E2E=1`, against a site
built with `NEXT_PUBLIC_TURNSTILE_SITE_KEY=1x00000000000000000000AA` and started with
`TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA`: Cloudflare's public test keys, as
CI uses. The API keeps its lookup thresholds in memory: restart it if repeated local runs
start being asked for the check.

In the Claude Code cloud environment Chromium is pre-installed (`PLAYWRIGHT_BROWSERS_PATH`).
Elsewhere, run `npx playwright install chromium` once.

## Visual snapshots

`scripts/visual.sh` compares every page and every world with the pictures in
`e2e/visual.spec.ts-snapshots/` (`--update` retakes them). It needs Docker, the backend's API on
:8000 and `DATABASE_URL`: it builds the site again with `NEXT_PUBLIC_WORLDS=off` into
`.next-off`, serves it on :3001, reseeds the recorded game as live, final, stale and pregame,
and runs `e2e/visual.spec.ts` inside Playwright's container image, so the browser and fonts
are the same everywhere. Set `PYTHON` to the backend's interpreter (`../backend/.venv/bin/python`).

## Colours

Every colour is in `lib/tokens.ts`. `npm test` checks each pairing against WCAG AA and fails
if `app/globals.css` names a colour of its own; `e2e/audit.spec.ts` checks the pages as drawn.
