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

`e2e/challenge.spec.ts` (the Turnstile check) runs only with `TURNSTILE_E2E=1`, against a site
built with `NEXT_PUBLIC_TURNSTILE_SITE_KEY=1x00000000000000000000AA` and started with
`TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA`: Cloudflare's public test keys, as
CI uses. The API keeps its lookup thresholds in memory: restart it if repeated local runs
start being asked for the check.

In the Claude Code cloud environment Chromium is pre-installed (`PLAYWRIGHT_BROWSERS_PATH`).
Elsewhere, run `npx playwright install chromium` once.
