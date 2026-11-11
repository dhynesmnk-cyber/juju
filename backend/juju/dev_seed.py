"""Development and end-to-end tests only: load the recorded PHI @ CHI game (2026-09-28).

The prices are the real recorded ones and are stored through `store_snapshot`, like a real
capture. The *times* are moved for the `live` scenario so the game is in play now; that makes
the seeded data a demo, which is why this module reads fixtures from `tests/` and the
production image doesn't ship them.

- `final`: the real timeline. The odds were taken about two hours before kickoff, so every card
  is EARLY (flagged). The game is final and settled.
- `live`: kickoff an hour ago, odds observed 46 minutes before it (ON_TIME), the real box score,
  and the game in the fourth quarter.
- `stale`: `live`, last read six minutes ago: the feed is down, so every card says its live
  status is unavailable (and still shows its price).
- `pregame`: kickoff in half an hour, the same odds observed 46 minutes before it, and nothing
  played yet. It clears the plays and stats an earlier seed of the game left behind.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from juju.core.enums import DataSource, EventStatus, SnapshotSource
from juju.core.models import DecidingPlay, Game, LiveStat, OddsSnapshot, Play, StatCorrection
from juju.ingest import espn
from juju.ingest.odds_api import ApiEvent, EventOdds, OddsResponse
from juju.worker.archive import store_snapshot
from juju.worker.live import write_box, write_plays
from juju.worker.schedule import apply_scoreboard, upsert_odds_events, upsert_roster

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SCENARIOS = ("live", "final", "stale", "pregame")
ODDS_ID = "47dc7baa254659f3beb2ed2b38c207b6"
RECORDED_KICKOFF = datetime.fromisoformat("2026-09-29T00:15:00+00:00")
RECORDED_ODDS_AT = datetime.fromisoformat("2026-09-28T22:08:35+00:00")


def _load(*parts: str):
    return json.loads(FIXTURES.joinpath(*parts).read_text())


def seed_phi_chi_scenario(session: Session, scenario: str, now: datetime) -> Game:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario is one of {', '.join(SCENARIOS)}")
    events = [ApiEvent.model_validate(e) for e in _load("odds_api", "nfl_events_2026-09-28.json")]
    upsert_odds_events(session, events[:1], now)
    board = espn.parse_scoreboard(espn.Sport.NFL,
                                  _load("espn", "nfl_scoreboard_2026-09-28_final.json"))
    apply_scoreboard(session, board.games)
    for team in ("21", "3"):
        upsert_roster(session, team, espn.parse_roster(_load("espn", f"nfl_roster_{team}.json")),
                      now)
    game = session.scalars(select(Game).where(Game.odds_event_id == ODDS_ID)).one()
    session.execute(delete(OddsSnapshot).where(OddsSnapshot.game_id == game.id))

    if scenario == "final":
        kickoff, observed = RECORDED_KICKOFF, RECORDED_ODDS_AT
    else:
        kickoff = now + timedelta(minutes=30) if scenario == "pregame" else now - timedelta(hours=1)
        observed = kickoff - timedelta(minutes=46)
    game.commence_time = kickoff
    raw = _load("odds_api", "nfl_event_odds_2026-09-28_PHI-CHI.json")
    raw["commence_time"] = kickoff.isoformat().replace("+00:00", "Z")
    text = json.dumps(raw)
    markets = sorted({m["key"] for b in raw["bookmakers"] for m in b["markets"]})
    store_snapshot(session, game, OddsResponse(EventOdds.model_validate(raw), text, None),
                   source=SnapshotSource.LIVE, slot="t47", fetched_at=observed, markets=markets,
                   books=sorted({b["key"] for b in raw["bookmakers"]}))

    if scenario == "pregame":
        for model in (Play, LiveStat, DecidingPlay, StatCorrection):
            session.execute(delete(model).where(model.game_id == game.id))
        game.status, game.period, game.clock_seconds = EventStatus.SCHEDULED, None, None
        game.home_score = game.away_score = game.final_at = None
        game.last_polled_at = game.last_box_at = game.last_progress_at = None
        session.commit()
        return game

    summary = _load("espn", "nfl_summary_401872963_full.json")
    box = espn.parse_box_score(summary)
    write_box(session, game, box, DataSource.ESPN_WEB, now)
    write_plays(session, game, espn.parse_plays(summary))
    game.home_score, game.away_score = box.home_score, box.away_score
    if scenario == "final":
        game.status, game.period, game.clock_seconds = EventStatus.FINAL, 4, 0
        game.final_at = kickoff + timedelta(hours=3, minutes=10)
    else:
        game.status, game.period, game.clock_seconds = EventStatus.IN_PROGRESS, 4, 312
        game.final_at = None
    # A game in play goes red five minutes after its last read (core/live.py).
    read_at = now - timedelta(minutes=6) if scenario == "stale" else now
    game.last_polled_at = game.last_box_at = read_at
    game.last_progress_at = read_at
    session.commit()
    return game
