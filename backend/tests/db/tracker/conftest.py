# Ported from parlaytracker@c3bd43c tests/db/conftest.py. Changes: imports; `seeded_books` is new,
# because Juju's `db` fixture empties every table after its tests, sportsbooks included; and
# `clean` waits for the worker's tests (Phase 2), where Juju's `db` fixture does its job.
"""Fixtures for the tracker's tests against the real PostgreSQL the migrations build."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from juju.tracker.models import Event, Sport, Sportsbook, Tag

# What migration 0006 seeds.
SEEDED_BOOKS = [
    {"name": "DraftKings", "odds_api_key": "draftkings"},
    {"name": "FanDuel", "odds_api_key": "fanduel"},
    {"name": "BetMGM", "odds_api_key": "betmgm"},
    {"name": "Caesars", "odds_api_key": "williamhill_us"},
]


@pytest.fixture(autouse=True)
def seeded_books(engine: Engine) -> None:
    """Put back the sportsbooks migration 0006 seeds, if another test's TRUNCATE took them."""
    with engine.begin() as conn:
        conn.execute(insert(Sportsbook).values(SEEDED_BOOKS).on_conflict_do_nothing())


@pytest.fixture
def session(engine: Engine) -> Session:
    """Everything a test does is rolled back afterwards; commits become savepoints."""
    with engine.connect() as conn:
        outer = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield s
        finally:
            s.close()
            outer.rollback()


@pytest.fixture
def book(session: Session) -> Sportsbook:
    return session.scalars(select(Sportsbook).where(Sportsbook.name == "DraftKings")).one()


def _event(session: Session, sport: Sport, espn_id: str) -> Event:
    event = Event(
        sport=sport,
        espn_event_id=espn_id,
        home_team="Home Team",
        away_team="Away Team",
        home_espn_team_id="1",
        away_espn_team_id="2",
        start_time=datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    )
    session.add(event)
    session.flush()
    return event


@pytest.fixture
def nfl_event(session: Session) -> Event:
    return _event(session, Sport.NFL, "401001")


@pytest.fixture
def nfl_event_2(session: Session) -> Event:
    return _event(session, Sport.NFL, "401002")


@pytest.fixture
def nba_event(session: Session) -> Event:
    return _event(session, Sport.NBA, "401003")


@pytest.fixture
def tag(session: Session) -> Tag:
    t = Tag(category="situation", name="primetime")
    session.add(t)
    session.flush()
    return t
