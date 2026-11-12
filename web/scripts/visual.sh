#!/usr/bin/env bash
# Visual snapshots (e2e/visual.spec.ts): every page and every world, as still pictures.
#
#   scripts/visual.sh            compare with the baselines in e2e/visual.spec.ts-snapshots/
#   scripts/visual.sh --update   take them again, after a change of look that is meant
#
# Needs: the backend's API on :8000 with DATABASE_URL set (it reseeds the recorded game four
# times: live, final, stale, pregame, and leaves it live), Docker, and `npm ci` done. It builds
# the site with NEXT_PUBLIC_WORLDS=off into .next-off, serves it on :3001, and runs Playwright
# inside its own image so the browser and the fonts are the same here and in CI. The image's
# version must be package.json's @playwright/test.
set -euo pipefail
cd "$(dirname "$0")/.."
UPDATE=""
[ "${1:-}" = "--update" ] && UPDATE="--update-snapshots"
VERSION="$(node -p "require('./package.json').devDependencies['@playwright/test']")"
IMAGE="mcr.microsoft.com/playwright:v${VERSION}-noble"
PYTHON="${PYTHON:-python}"
PORT=3001

NEXT_PUBLIC_WORLDS=off NEXT_DIST_DIR=.next-off npx next build > /dev/null
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/about" 2>/dev/null; then
  echo "something is already serving on :$PORT; stop it first" >&2
  exit 1
fi
# Node itself, not npx; `next start` serves from a child, which is stopped first.
NEXT_DIST_DIR=.next-off BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}" \
  node node_modules/next/dist/bin/next start -p "$PORT" > /dev/null 2>&1 &
SITE=$!
trap 'pkill -P "$SITE" 2>/dev/null || true; kill "$SITE" 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/about" 2>/dev/null && break; sleep 1; done

status=0
for state in live final stale pregame; do
  (cd ../backend && "$PYTHON" -m juju.cli seed "$state" > /dev/null)
  # The card endpoints of a final game are cached for five minutes by nothing here: the
  # site asks the API afresh on every page.
  docker run --rm --network host --ipc host -v "$PWD":/work -w /work \
    -e VISUAL="$state" -e VISUAL_BASE_URL="http://127.0.0.1:$PORT" -e CI="${CI:-}" \
    "$IMAGE" npx playwright test e2e/visual.spec.ts --trace off $UPDATE || status=1
done
(cd ../backend && "$PYTHON" -m juju.cli seed live > /dev/null)
exit "$status"
