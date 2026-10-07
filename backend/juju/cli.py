"""Command line: `python -m juju.cli <command>`.

  backfill SINCE UNTIL   Fill games between two dates (YYYY-MM-DD) from The Odds API's
                         history. Costs credits: about 10 per market per game, plus 1 per day.
                         Prints the estimate and asks before spending.
  unmapped               Odds API player names not matched to a roster player.
  verify                 Check last week's final games against nflverse now (free), instead of
                         waiting for the worker's 10:07 or 16:07 ET run.
  seed [live|final]      Development and tests only: load the recorded PHI @ CHI game.
"""
import logging
import sys
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
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
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
