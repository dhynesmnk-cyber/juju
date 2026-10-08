"""Command line: `python -m juju.cli <command>`.

  backfill SINCE UNTIL   Fill games between two dates (YYYY-MM-DD) from The Odds API's
                         history. Costs credits: about 10 per market per game, plus 1 per day.
                         Prints the estimate and asks before spending.
  unmapped               Odds API player names not matched to a roster player.
  verify                 Check last week's final games against nflverse now (free), instead of
                         waiting for the worker's 10:07 or 16:07 ET run.
  seed [live|final]      Development and tests only: load the recorded PHI @ CHI game.

The tracker's (the private section: docs/plans/integrate-parlaytracker.md):
  import-slips CSV --user NAME [--apply] [--include-doubtful]
                         Plan the slips a CSV transcribes against ESPN and, with --apply, log
                         the sound ones as NAME's. A dry run without --apply. CSV may be `-`
                         for standard input (the laptop's containers can't see its files).
  check-import CSV       Compare the settled slips with what the CSV says the sportsbook paid;
                         exits 1 on any mismatch.
  tracker-backfill       Settle everything logged before the worker could: which old games
                         finished, their legs, then NFL against nflverse (free). Stop the
                         worker first; it refuses to run beside it.
  export-sample ID PATH  Write a saved raw response (raw_samples.id) out as a test fixture.
  export-recording EVENT DIR
                         Write a game recorded with RECORD_EVENT_IDS out for replay in tests.
"""
import logging
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from juju.config import get_settings
from juju.db import make_engine


def _engine():
    return make_engine()


def backfill(since: date, until: date, assume_yes: bool = False) -> int:
    from juju.core.markets import ALL_KEYS
    from juju.ingest.odds_api import OddsApiClient
    from juju.ingest.router import Breakers
    from juju.worker.capture import HISTORICAL_MULTIPLIER, RepairGaps
    from juju.worker.schedule import upsert_odds_events

    settings = get_settings()
    if settings.odds_api_key is None:
        print("ODDS_API_KEY is not set")
        return 2
    engine = _engine()
    breakers = Breakers(engine)
    breakers.load()
    odds = OddsApiClient(settings.odds_api_key.get_secret_value(), breakers)
    days = (until - since).days + 1
    per_game = HISTORICAL_MULTIPLIER * len(ALL_KEYS)
    print(f"{days} days: {days} credits to list the games, then up to {per_game} per game not "
          f"already on file (about {per_game * 16 * days // 7} for a full NFL schedule).")
    if not assume_yes and input("Spend these credits? [y/N] ").strip().lower() != "y":
        return 1
    ids: list[int] = []
    with Session(engine) as session:
        for n in range(days):
            day = since + timedelta(days=n)
            events = [e for e in odds.historical_events(datetime.combine(day, time(12), UTC))
                      if e.commence_time.date() in (day, day + timedelta(days=1))]
            upsert_odds_events(session, events, datetime.now(tz=UTC))
            session.commit()
        from juju.core.models import Game
        ids = list(session.scalars(select(Game.id).where(Game.commence_time.between(
            datetime.combine(since, time(0), UTC), datetime.combine(until, time(23, 59), UTC)))))
    repaired = RepairGaps(engine, odds, settings.books, settings.odds_api_reserve,
                          settings.repair_days)(ids)
    print(f"repaired {repaired} of {len(ids)} games; {odds.quota_remaining} credits left")
    return 0


def unmapped() -> int:
    from juju.core.models import Game, PlayerNameMap
    with Session(_engine()) as session:
        rows = session.execute(select(Game, PlayerNameMap).join(
            PlayerNameMap, PlayerNameMap.game_id == Game.id).where(
            PlayerNameMap.status != "auto").order_by(Game.commence_time)).all()
        for game, m in rows:
            print(f"{game.label:12} {m.status:9} {m.raw_name!r} (score {m.score})")
        print(f"{len(rows)} names")
    return 0


def verify() -> int:
    from juju.ingest.nflverse import NflverseData
    from juju.ingest.router import Breakers
    from juju.worker.verify import VerifyGames

    engine = _engine()
    breakers = Breakers(engine)
    breakers.load()
    out = VerifyGames(engine, lambda: NflverseData(breakers))()
    print(f"verified {out.verified} games, corrected {out.corrected} stats, found "
          f"{out.plays} deciding plays ({out.late} games' play-by-play came late); "
          f"{out.waiting} not published yet, {out.disputed} disputed")
    return 0


def seed(scenario: str = "live") -> int:
    from juju.dev_seed import seed_phi_chi_scenario
    with Session(_engine()) as session:
        game = seed_phi_chi_scenario(session, scenario, datetime.now(tz=UTC))
        print(f"seeded {game.label} as {scenario} (game id {game.id})")
    return 0


# --- The tracker's commands (ported from parlaytracker@c3bd43c parlaytracker/cli.py; changes:
# ESPN through Juju's router: in-memory breakers for the importer, so a run never touches the
# worker's, and the worker's own for the backfill, which takes its lock; and `backfill` is
# `tracker-backfill`, because Juju's `backfill` is the odds archive's) -----------------------


def _read(path: Path) -> str:
    """A file, or standard input for `-`: the laptop's containers can't see its files."""
    return sys.stdin.read() if str(path) == "-" else path.read_text()


def import_slips(engine: Engine, path: Path, user: str, apply: bool, include_doubtful: bool,
                 games_for=None, roster_for=None) -> int:
    """Plan the CSV's slips against ESPN and, with `apply`, write the ones that are sound."""
    from juju.ingest.router import Breakers, EspnRouter
    from juju.tracker import fetch, slip_import

    wait = 30.0  # a run of requests may have to wait for ESPN's one-per-2-seconds slots
    router = EspnRouter(Breakers(engine=None), default_max_wait=wait)
    games_for = games_for or (lambda sport, day: fetch.scoreboard(router, sport, day, wait).games)
    roster_for = roster_for or (lambda sport, team: fetch.roster(router, sport, team, wait))
    try:
        slips = slip_import.parse_csv(_read(path))
    except (OSError, slip_import.CsvError) as e:
        print(f"can't read {path}: {e}", file=sys.stderr)
        return 1
    with Session(engine) as session:
        plans = slip_import.plan_import(session, slips, games_for=games_for,
                                        roster_for=roster_for)
        print(slip_import.summarize(plans))
        if not apply:
            print("\ndry run: nothing was written. Add --apply to import the ok slips"
                  + (" (and, with --include-doubtful, the doubtful ones)." if not include_doubtful
                     else "."))
            return 0
        report = slip_import.apply_plans(session, plans, user, include_doubtful=include_doubtful)
        session.commit()
    print(f"\nimported {len(report.imported)} slip(s); skipped "
          f"{len(report.skipped)}: {report.skipped or 'none'}")
    return 0


def check_import(engine: Engine, path: Path) -> int:
    from juju.tracker import slip_import

    try:
        slips = slip_import.parse_csv(_read(path))
    except (OSError, slip_import.CsvError) as e:
        print(f"can't read {path}: {e}", file=sys.stderr)
        return 1
    with Session(engine) as session:
        report = slip_import.check_import(session, slips)
    print(f"{report.slips} slips in the database; legs: {report.legs_agree} agree, "
          f"{report.legs_pending} not settled yet, {report.legs_review} in Review; "
          f"slips agreeing: {report.slips_agree}")
    if report.not_imported:
        print(f"not imported: {', '.join(report.not_imported)}")
    for m in report.mismatches:
        print(f"MISMATCH {m.slip_id}: {m.what}")
    for note in report.payout_notes:
        print(f"payout: {note}")
    return 1 if report.mismatches else 0


def tracker_backfill(engine: Engine, router, nflverse) -> int:
    """Find out which old games finished, settle their legs, then check NFL against nflverse.

    A game found final long after the fact is dated to its expected end, so the ten-minute
    gate holds honestly. Safe to run more than once: settled legs are never touched.
    """
    from juju.tracker.worker.settle import CheckFinals, Settle, VerifyNfl

    finals = CheckFinals(engine, router)(force=True, backfill=True)
    if finals.skipped:
        # Settling now would flag a game "not final 8 hours after its start" that ESPN simply
        # couldn't be asked about. Nothing has been settled or flagged: run it again.
        print(f"{finals.skipped} game(s) not reachable on ESPN: nothing settled. Run the "
              "backfill again in a few minutes.", file=sys.stderr)
        return 1
    settled = Settle(engine, router)()
    verified = VerifyNfl(engine, nflverse)()
    print(f"games not yet reachable: {finals.skipped}")
    print(f"settled from ESPN: {settled.settled}, flagged for Review: {settled.flagged}")
    print(f"NFL: settled from nflverse {verified.settled}, verified {verified.verified}, "
          f"flagged {verified.flagged}, left unverified {verified.skipped}")
    return 0


def _tracker_backfill() -> int:
    """`tracker-backfill` with the worker's lock, router and breakers."""
    from juju.ingest.router import Breakers, EspnRouter
    from juju.tracker.feed import EspnFeed
    from juju.tracker.nflverse import NflverseLegs
    from juju.tracker.worker.settle import sample_sink
    from juju.worker.__main__ import try_lock

    engine = _engine()
    lock_conn = engine.connect()  # the worker's lock: two writers would poll ESPN twice
    if not try_lock(lock_conn):
        print("the worker is running: stop it first (`docker compose -f "
              "deploy/laptop/docker-compose.yml stop worker`), run the backfill, then start "
              "it again", file=sys.stderr)
        return 1
    breakers = Breakers(engine)
    breakers.load()
    router = EspnRouter(breakers, sample_sink=sample_sink(engine), default_max_wait=30.0)
    try:
        return tracker_backfill(engine, EspnFeed(router), lambda: NflverseLegs(breakers))
    finally:
        breakers.persist()
        lock_conn.close()


def export_sample(engine: Engine, sample_id: int, path: Path) -> int:
    """Write `raw_samples.body` to `path`, ready to drop into tests/fixtures/."""
    from juju.tracker.models import RawSample

    with Session(engine) as session:
        sample = session.get(RawSample, sample_id)
        if sample is None:
            print(f"no raw sample with id {sample_id}", file=sys.stderr)
            return 1
        path.write_text(sample.body)
        print(f"wrote {len(sample.body)} bytes ({sample.source}, {sample.reason}, "
              f"{sample.fetched_at:%Y-%m-%d %H:%M}) to {path}")
    return 0


def export_recording(engine: Engine, espn_event_id: str, directory: Path) -> int:
    """Write every recording of one game, in the order it was saved, as `NNNN_source_kind.json`
    plus an `index.json`: a real game to replay through the tracker's live poll."""
    import json

    from juju.tracker.models import RawSample

    with Session(engine) as session:
        samples = session.scalars(
            select(RawSample).where(RawSample.reason == "recording",
                                    RawSample.espn_event_id == espn_event_id)
            .order_by(RawSample.id)).all()
        if not samples:
            print(f"no recordings for event {espn_event_id}: set RECORD_EVENT_IDS before the "
                  "game and restart the worker", file=sys.stderr)
            return 1
        directory.mkdir(parents=True, exist_ok=True)
        index = []
        for n, sample in enumerate(samples, start=1):
            kind = "scoreboard" if sample.url.rstrip("/").endswith("scoreboard") else "summary"
            name = f"{n:04d}_{sample.source}_{kind}.json"
            (directory / name).write_text(sample.body)
            index.append({"file": name, "source": sample.source, "kind": kind,
                          "url": sample.url, "fetched_at": sample.fetched_at.isoformat()})
        (directory / "index.json").write_text(json.dumps(index, indent=2))
        print(f"wrote {len(samples)} responses to {directory}")
    return 0


def _option(args: list[str], name: str) -> str | None:
    """The value after `name` in `args`, if both are there."""
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return None


def _positional(args: list[str]) -> list[str]:
    """`args` without the --flags and the value after --user (`-` is standard input)."""
    out, skip = [], False
    for arg in args:
        if skip or arg == "--user":
            skip = not skip
        elif not arg.startswith("--"):
            out.append(arg)
    return out


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "backfill" and len(args) >= 2:
        return backfill(date.fromisoformat(args[0]), date.fromisoformat(args[1]),
                        "--yes" in args)
    if cmd == "unmapped":
        return unmapped()
    if cmd == "verify":
        return verify()
    if cmd == "seed":
        return seed(args[0] if args else "live")
    files, user = _positional(args), _option(args, "--user")
    if cmd == "import-slips" and len(files) == 1 and user:
        return import_slips(_engine(), Path(files[0]), user, "--apply" in args,
                            "--include-doubtful" in args)
    if cmd == "check-import" and len(files) == 1:
        return check_import(_engine(), Path(files[0]))
    if cmd == "tracker-backfill":
        return _tracker_backfill()
    if cmd == "export-sample" and len(args) == 2 and args[0].isdigit():
        return export_sample(_engine(), int(args[0]), Path(args[1]))
    if cmd == "export-recording" and len(args) == 2:
        return export_recording(_engine(), args[0], Path(args[1]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
