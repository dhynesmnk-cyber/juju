# juju-web

The public Next.js site. It talks to the backend only through `app/api/[...path]/route.ts`,
which forwards an allowlist of UI endpoints (docs/licensing.md).

```bash
npm ci
npm run lint && npm run typecheck && npm run build

# End-to-end, at phone width, against the recorded PHI @ CHI game:
cd ../backend && DATABASE_URL=<dev db> .venv/bin/python -m juju.cli seed live
DATABASE_URL=<dev db> .venv/bin/uvicorn juju.api.app:app --port 8000 &
cd ../web && BACKEND_URL=http://127.0.0.1:8000 npx next start -p 3000 &
npx playwright test
```

In the Claude Code cloud environment Chromium is pre-installed (`PLAYWRIGHT_BROWSERS_PATH`).
Elsewhere, run `npx playwright install chromium` once.
