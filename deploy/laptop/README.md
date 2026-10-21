# Juju on your own machine (private preview)

One script puts the whole of Juju on an Ubuntu machine you own: Postgres, the API, the worker
and the site, each in Docker. The site goes on the internet through Tailscale Funnel, behind a
passcode you share with the people you choose. The API and the database are never reachable from
outside the machine. It costs nothing but electricity. It's for a preview: when the machine
sleeps or loses its connection, the site is down. For launch, use Fly.io (`docs/deploy.md`).

## What you need

- Ubuntu 22.04 or later, with about 2 GB of memory and 5 GB of free disk.
- Docker and Compose:
  ```bash
  sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git curl
  sudo usermod -aG docker $USER      # then log out and back in
  ```
- Tailscale, logged in on this machine, with these turned on in the admin console:
  **MagicDNS** and **HTTPS certificates** (the DNS page), and **Funnel** for this machine. If
  Funnel isn't allowed yet, the first run prints a link that turns it on, and waits.
- `sudo` on this machine: changing Funnel needs it, so the script asks for your password then.

## Put it online

```bash
git clone https://github.com/dhynesmnk-cyber/juju.git
cd juju
deploy/laptop/up.sh
```

The first run asks for a passcode (10 or more letters, digits, `-`, `_` or `.`), or makes one up
if you press Enter. Then it asks for the owners' names and a passcode for each, for your own
lookups and recaps (below); Enter makes those up too. It builds everything (several minutes the first time), loads the recorded
demo game, checks that the site is locked, that the passcode opens it and that the pages work,
then puts it online and prints:

- the address, `https://<this machine>.<your tailnet>.ts.net`;
- the passcode, if it made one up (it is kept in `deploy/laptop/.env`, readable only by you);
- the owners' passcodes, if it made them up (also in `.env`, as `TRACKER_USERS`);
- two links to the demo game (PHI @ CHI, Sep 28). The home page lists this week's games
  (Tuesday to Monday), so open the demo from these links.

Send the address and the passcode to your two users. Each browser asks for the passcode once,
then remembers it for 30 days.

## What you'll see

- **The demo game:** real prices recorded about two hours before kickoff, so each card flags
  them as earlier than T-45. Its cards say "Awaiting verification": the next-day check only
  looks at games from the last week.
- **Real games:** the worker follows this week's NFL games from ESPN, so they show up on the
  home page with live plays. Their prices are saved only with Juju's own Odds API key (below);
  without one, their cards say "No price on file" and the site says prices aren't being saved.

## Your lookups (the owners only)

Each owner signs in once per phone at `<address>/me`, with their own name and passcode. From
then on, every bet page they open is kept under their name: a player's or team's bets, a play
tapped open on a game's page, a parlay. `/me` lists the games, each with its recap: every bet
looked up, the price 45 minutes before kickoff, how it went and what $10 came back as, worked
out again from the archive each time it is opened. A player's page also shows how earlier
lookups of that player went, and the home page shows the recaps of games that just finished.
Only the owner signed in sees any of it; the two users see none of it, and nothing from it is
public. "Remove" takes a lookup out of a recap.

## Day to day

| To | Run, from the `juju` folder |
|---|---|
| Update to the latest code | `git pull && deploy/laptop/up.sh` |
| See the logs | `docker compose -f deploy/laptop/docker-compose.yml logs -f web api worker` |
| Change the passcode | edit `SITE_PASSCODE` in `deploy/laptop/.env`, then `deploy/laptop/up.sh` (everyone enters the new one) |
| Change an owner's passcode | edit that owner's `name:passcode` in the `TRACKER_USERS` line of `deploy/laptop/.env`, then `deploy/laptop/up.sh` (only that owner signs in again) |
| Take it off the internet | `sudo tailscale funnel --https=443 http://127.0.0.1:3000 off` (it keeps running on this machine; `tailscale funnel status` shows what's online) |
| Stop it | `docker compose -f deploy/laptop/docker-compose.yml down` (keeps the data) |
| Back up the database | `docker compose -f deploy/laptop/docker-compose.yml exec -T db pg_dump -U juju juju \| gzip > juju-$(date +%F).sql.gz` |
| Remove it, data and all | `docker compose -f deploy/laptop/docker-compose.yml down -v` |

Start and update it only with `up.sh`: it runs Docker with nothing from your shell, so a key you
have exported for something else can't end up in Juju, and it checks everything afterwards. If
an update ever leaves the site unlocked, `up.sh` takes it off the internet before it stops.

After a reboot, Docker starts Juju again and Funnel stays on.

## Keep the machine awake

A sleeping laptop takes the site down. To keep it running with the lid closed:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\n' \
  | sudo tee /etc/systemd/logind.conf.d/juju.conf
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

The lid setting takes effect after a reboot. To undo: delete that file and
`sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target`.

## Real prices

With Juju's own Odds API key, put one line in `deploy/laptop/.env` (to change keys, replace
that line; don't add a second):

```
JUJU_ODDS_API_KEY=<the key>
```

and run `deploy/laptop/up.sh`. It checks the key with one free call and prints the credits left
(and where that number came from), when they reset, and what the captures will take. The worker
then saves prices twice before each kickoff, at 3 hours and at 45 minutes (about 50 credits a
game). Never use parlaytracker's key here, and never paste a key into a chat.

A new key's credits are unknown until its first capture: Juju never carries an old key's count
over, and forgets last month's count when the credits reset. If `up.sh` prints "Captures: none",
it adds a note: the key in `.env` has no credits left until they reset.

Any plan works, even a free one: when credits run short the worker saves fewer bets and the
site says so. Three optional lines in `.env` tune it:

| Line | What it does |
|---|---|
| `ODDS_API_RESET_DAY=15` | The day of the month your plan's credits reset (1 to 28; default 1) |
| `REPAIR_DAYS=2` | How far back a game with no 45-minute price is filled from the vendor's history, at about 340 credits a game (default 14; 0 turns it off; paid plans only) |
| `ODDS_API_RESERVE=1000` | Credits kept back: only the 45-minute capture may spend them |

To see what the worker saved and what people looked up since a day:
`docker compose -f deploy/laptop/docker-compose.yml exec api python -m juju.cli report --since 2026-10-18`.

## If something goes wrong

`up.sh` stops at the first problem and says what to do. If that isn't enough, paste its output
(it never prints the database password, and prints the passcode only when it has just made one
up) into a Claude Code session on this repository: the `deploy-laptop` skill knows this setup.
