#!/usr/bin/env bash
# Put Juju online from this machine, for a private preview: Postgres, the API, the worker and
# the site in Docker, the site behind a passcode, reachable through Tailscale Funnel.
# Safe to run again: it keeps .env and the database, rebuilds what changed, and checks it all.
#
#   ./up.sh              set up or update, check it, put it online
#   ./up.sh --no-funnel  the same without Tailscale: the site stays on http://127.0.0.1:3000
#
# README.md next to this file says more. Nothing here prints a secret, except a passcode it has
# just made up for you.
set -euo pipefail

cd "$(dirname "$0")"
HERE=$PWD
REPO=$(cd ../.. && pwd)
ENV_FILE=$HERE/.env
DEMO_EVENT=401872963   # PHI @ CHI, 2026-09-28: the recorded game (backend/tests/fixtures)
DEMO_PLAYER=3929630    # Saquon Barkley: his page is checked at the end
LOCAL=http://127.0.0.1:3000
FUNNEL=1
for arg in "$@"; do
  case $arg in
    --no-funnel) FUNNEL=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
note() { printf '   note: %s\n' "$*"; }
die() { printf '\n\033[31mStopped: %s\033[0m\n' "$*" >&2; exit 1; }
# Compose takes a variable from the shell before .env. It runs with only what Docker itself
# needs, so nothing exported in this shell (an Odds API key, say) can slip into the stack.
compose() {
  env -i PATH="$PATH" HOME="$HOME" \
    ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} ${DOCKER_CONTEXT:+DOCKER_CONTEXT="$DOCKER_CONTEXT"} \
    ${DOCKER_CONFIG:+DOCKER_CONFIG="$DOCKER_CONFIG"} \
    ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
    docker compose --env-file "$ENV_FILE" -f "$HERE/docker-compose.yml" "$@"
}
get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }
put() {  # set KEY=value in .env, keeping the file readable only by you
  local tmp
  tmp=$(mktemp "$HERE/.env.XXXXXX")
  grep -vE "^$1=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}
sql() { compose exec -T db psql -U juju -d juju -tAc "$1"; }
lcurl() { curl --noproxy '*' -s "$@"; }  # this machine's own site, never through a proxy
# Changing Funnel needs root. Its output stays visible: when Funnel isn't allowed yet, Tailscale
# prints a link to allow it and waits.
funnel() { sudo tailscale funnel --https=443 "$@"; }

# A failed safety check fails closed. Funnel stays on between runs, so an update that broke the
# gate would otherwise be online, unlocked: take the site off the internet first, then stop.
unsafe() {
  if [ "$FUNNEL" = 1 ]; then
    if funnel "$LOCAL" off > /dev/null 2>&1; then
      printf '\n   The site is off the internet. It comes back with the next good run.\n' >&2
    elif tailscale funnel status 2> /dev/null | grep -q "Funnel on"; then
      printf '\n   TAKE IT OFFLINE NOW: sudo tailscale funnel --https=443 %s off\n' "$LOCAL" >&2
    fi
  fi
  die "$1"
}

# --- 1. This machine -------------------------------------------------------------------------
step "Checking this machine"
command -v docker > /dev/null || die "Docker isn't installed. On Ubuntu:
     sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
     sudo usermod -aG docker \$USER     # then log out and back in
   and run ./up.sh again."
docker compose version > /dev/null 2>&1 \
  || die "Docker Compose v2 is missing: sudo apt-get install -y docker-compose-v2"
docker info > /dev/null 2>&1 || die "Docker isn't running, or this user can't use it:
     sudo systemctl enable --now docker
     sudo usermod -aG docker \$USER     # then log out and back in"
ok "Docker $(docker version --format '{{.Server.Version}}')"
for tool in curl openssl python3; do
  command -v "$tool" > /dev/null || die "$tool is missing: sudo apt-get install -y $tool"
done
mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
[ "$mem_mb" -ge 1800 ] || note "${mem_mb} MB of memory; building the site needs about 2 GB. If the build gets killed, add swap."
docker_root=$(docker info --format '{{.DockerRootDir}}' 2> /dev/null || echo /var/lib/docker)
free_gb=$(df -Pk "$docker_root" 2> /dev/null | awk 'NR == 2 {print int($4 / 1048576)}')
[ "${free_gb:-99}" -ge 5 ] || die "only ${free_gb} GB free where Docker keeps images ($docker_root); they need about 5 GB."

dns=""
if [ "$FUNNEL" = 1 ]; then
  command -v tailscale > /dev/null \
    || die "Tailscale isn't installed (https://tailscale.com/download/linux). Or run ./up.sh --no-funnel."
  dns=$(tailscale status --json 2> /dev/null | python3 -c \
    'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2> /dev/null || true)
  [ -n "$dns" ] || die "Tailscale isn't logged in here, or MagicDNS is off. Run 'sudo tailscale up',
   turn on MagicDNS and HTTPS certificates in the admin console (DNS page), then ./up.sh again."
  ok "Tailscale: this machine is $dns"
fi

# --- 2. Settings -----------------------------------------------------------------------------
step "Settings (deploy/laptop/.env, readable only by you)"
umask 077
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
[ -n "$(get POSTGRES_PASSWORD)" ] || put POSTGRES_PASSWORD "$(openssl rand -hex 24)"
new_passcode=""
if [ -z "$(get SITE_PASSCODE)" ]; then
  code=""
  if [ -t 0 ]; then
    echo "   Choose the passcode you'll share: 10 or more letters, digits, - _ or ."
    read -r -p "   Or press Enter and one is made up for you: " code
  fi
  if [ -z "$code" ]; then
    code=$(python3 -c 'import secrets
a = "abcdefghjkmnpqrstuvwxyz23456789"
print("-".join("".join(secrets.choice(a) for _ in range(4)) for _ in range(3)))')
    new_passcode=$code
  fi
  [[ "$code" =~ ^[A-Za-z0-9._-]{10,200}$ ]] \
    || die "the passcode needs 10 or more characters: letters, digits, - _ or ."
  put SITE_PASSCODE "$code"
fi
if [ "$FUNNEL" = 1 ]; then
  put SITE_URL "https://$dns"
elif [ -z "$(get SITE_URL)" ]; then
  put SITE_URL "$LOCAL"
fi
ok "passcode, database password and address are set"

# --- 3. Build and start ----------------------------------------------------------------------
step "Building the images (the first time takes several minutes)"
compose build

step "Starting Postgres and running the migrations"
compose up -d --wait db
compose run --rm --no-deps migrate
ok "the database schema is current"

step "The recorded demo game (PHI @ CHI, Sep 28)"
if [ "$(sql "select count(*) from games where espn_event_id = '$DEMO_EVENT'")" = 0 ]; then
  # The production image doesn't ship the test fixtures; they are lent read-only, this once.
  compose run --rm --no-deps -v "$REPO/backend/tests:/app/tests:ro" migrate \
    python -m juju.cli seed final
else
  ok "already loaded"
fi

step "Starting the API, the worker and the site"
if ! compose ps --status running --services 2> /dev/null | grep -qx web \
    && (exec 3<> /dev/tcp/127.0.0.1/3000) 2> /dev/null; then
  die "something else is already using port 3000 on this machine. Stop it, then ./up.sh again."
fi
compose up -d --wait api worker web
ok "running: $(compose ps --status running --services | sort | tr '\n' ' ')"

# --- 4. Check it -----------------------------------------------------------------------------
step "Checking it works"
code=$(lcurl -o /dev/null -w '%{http_code}' "$LOCAL/api/status")
[ "$code" = 401 ] || unsafe "without the passcode the site answered $code, not 401: the passcode
   gate is off. Check SITE_PASSCODE in .env, then ./up.sh again."
page=$(lcurl "$LOCAL/about")
[[ "$page" == *'action="/unlock/check"'* && "$page" != *"How Juju works"* ]] \
  || unsafe "without the passcode a page didn't ask for it. See: docker compose logs web"
ok "locked without the passcode"

secret_file=$(mktemp "$HERE/.passcode.XXXXXX")
trap 'rm -f "$secret_file"' EXIT
printf '%s' "$(get SITE_PASSCODE)" > "$secret_file"
cookie=$(lcurl -D - -o /dev/null --data-urlencode "passcode@$secret_file" -d next=/ \
           "$LOCAL/unlock/check" | tr -d '\r' \
         | sed -n 's/^[Ss]et-[Cc]ookie: \(juju_pass=[^;]*\).*/\1/p')
rm -f "$secret_file"
[ -n "$cookie" ] || die "the passcode in .env didn't open the site. See: docker compose logs web"
ok "the passcode opens it"

status=$(lcurl -H "Cookie: $cookie" "$LOCAL/api/status")
[[ "$status" == *'"degraded"'* ]] \
  || die "the site can't reach the API ($status). See: docker compose logs api web"
ok "the site reaches the API"

game=$(sql "select id from games where espn_event_id = '$DEMO_EVENT'")
lcurl -H "Cookie: $cookie" "$LOCAL/g/$game/$DEMO_PLAYER" | grep -q "Saquon Barkley" \
  || die "the demo game's page didn't render. See: docker compose logs web api"
ok "the demo game renders"

for service in api db; do
  published=$(docker ps --filter "label=com.docker.compose.project=juju" \
                --filter "label=com.docker.compose.service=$service" --format '{{.Ports}}')
  [[ "$published" != *"->"* ]] \
    || unsafe "$service has a published port ($published). Only the site may (docs/licensing.md)."
done
ok "only the site has a port, and only on 127.0.0.1"

archive=""
for _ in $(seq 1 30); do  # the worker says which, a moment after it starts
  archive=$(compose logs --no-color worker 2> /dev/null \
              | grep -o 'worker started (archive [a-z]*' | tail -n 1 || true)
  [ -n "$archive" ] && break
  sleep 1
done
if [ -n "$(get JUJU_ODDS_API_KEY)" ]; then
  [[ "$archive" == *"archive on"* ]] \
    || die "JUJU_ODDS_API_KEY is in .env but the worker isn't archiving. See: docker compose logs worker"
  ok "the worker archives prices with Juju's Odds API key"
else
  [[ "$archive" == *"archive off"* ]] || {
    compose stop worker > /dev/null 2>&1 || true
    die "the worker started archiving without JUJU_ODDS_API_KEY in .env, so it found a key somewhere
   else; it has been stopped. Never use parlaytracker's key. See: docker compose logs worker"
  }
  ok "the worker archives nothing: no Juju Odds API key"
fi

# --- 5. Online -------------------------------------------------------------------------------
url=$LOCAL
if [ "$FUNNEL" = 1 ]; then
  step "Putting the site online with Tailscale Funnel (it needs sudo)"
  funnel --bg "$LOCAL" \
    || die "Funnel didn't start. If it printed a link, open it to allow Funnel for this machine
   (or in the admin console, Access controls: give it the funnel attribute), then ./up.sh again."
  url="https://$dns"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$url/" || true)
  if [ "$code" = 401 ]; then
    ok "$url answers, with the passcode form"
  else
    note "$url didn't answer from here yet ($code). The first certificate can take a minute; try it in a browser."
  fi
fi

step "Done"
echo "   Juju:      $url"
echo "   The demo game isn't on the home page (that lists today's games). Open it directly:"
echo "              $url/g/$game/team/3          PHI @ CHI, the Bears' lines"
echo "              $url/g/$game/$DEMO_PLAYER    Saquon Barkley's bets"
if [ -n "$new_passcode" ]; then
  echo "   Passcode:  $new_passcode   (made up just now; it's kept in deploy/laptop/.env)"
else
  echo "   Passcode:  the one in deploy/laptop/.env"
fi
echo "   Share both with your two users. Each browser asks once, then remembers for 30 days."
echo "   Update:    git pull && deploy/laptop/up.sh"
echo "   Logs:      docker compose -f deploy/laptop/docker-compose.yml logs -f web api worker"
echo "   More, including taking it offline: deploy/laptop/README.md"
