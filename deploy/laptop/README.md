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
  Funnel isn't allowed yet, the first run prints a link that turns it on.
- Optional, so Funnel doesn't ask for sudo: `sudo tailscale set --operator=$USER`.

## Put it online

```bash
git clone https://github.com/dhynesmnk-cyber/juju.git
cd juju
deploy/laptop/up.sh
```

The first run asks for a passcode (10 or more letters, digits, `-`, `_` or `.`), or makes one up
if you press Enter. It builds everything (several minutes the first time), loads the recorded
demo game, checks that the site is locked, that the passcode opens it and that the pages work,
then puts it online and prints:

- the address, `https://<this machine>.<your tailnet>.ts.net`;
- the passcode, if it made one up (it is kept in `deploy/laptop/.env`, readable only by you);
- two links to the demo game (PHI @ CHI, Sep 28). The home page lists only today's games, so
  open the demo from these links.

Send the address and the passcode to your two users. Each browser asks for the passcode once,
then remembers it for 30 days.

## What you'll see

- **The demo game:** real prices recorded about two hours before kickoff, so each card flags
  them as earlier than T-45. Its cards say "Awaiting verification": the next-day check only
  looks at games from the last week.
- **Real games:** the worker follows this week's NFL games from ESPN, so they show up on the
  home page with live plays. Their cards say "No price on file": prices are archived only with
  Juju's own Odds API key (below).

## Day to day

| To | Run, from the `juju` folder |
|---|---|
| Update to the latest code | `git pull && deploy/laptop/up.sh` |
| See the logs | `docker compose -f deploy/laptop/docker-compose.yml logs -f web api worker` |
| Change the passcode | edit `SITE_PASSCODE` in `deploy/laptop/.env`, then `deploy/laptop/up.sh` (everyone enters the new one) |
| Take it off the internet | `sudo tailscale funnel --https=443 off` (it keeps running on this machine) |
| Stop it | `docker compose -f deploy/laptop/docker-compose.yml down` (keeps the data) |
| Back up the database | `docker compose -f deploy/laptop/docker-compose.yml exec -T db pg_dump -U juju juju \| gzip > juju-$(date +%F).sql.gz` |
| Remove it, data and all | `docker compose -f deploy/laptop/docker-compose.yml down -v` |

Start and update it only with `up.sh`: it runs Docker with nothing from your shell, so a key you
have exported for something else can't end up in Juju, and it checks everything afterwards.

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

## Real prices, later

With Juju's own Odds API key (the 100K plan, `docs/deploy.md`), add one line to
`deploy/laptop/.env`:

```
JUJU_ODDS_API_KEY=<the key>
```

and run `deploy/laptop/up.sh`. The worker then archives prices before each kickoff. Never use
parlaytracker's key here.

## If something goes wrong

`up.sh` stops at the first problem and says what to do. If that isn't enough, paste its output
(it never prints the database password, and prints the passcode only when it has just made one
up) into a Claude Code session on this repository: the `deploy-laptop` skill knows this setup.
