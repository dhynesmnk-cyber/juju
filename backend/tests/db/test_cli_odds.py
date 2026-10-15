"""`odds-status` and `report` (juju/cli.py): what the owner runs on the laptop."""
from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from juju import cli
from juju.config import get_settings
from juju.core.models import Lookup
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import OddsApiClient
from juju.ingest.router import Breakers

pytestmark = pytest.mark.db

EVENTS = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events"
KEY = "not-a-real-key-5d1e"


@pytest.fixture
def settings(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client() -> OddsApiClient:
    return OddsApiClient(KEY, Breakers(engine=None), RateLimiter(per_host_interval=0,
                                                                  per_minute=1000))


@respx.mock
def test_a_working_key_shows_the_credits_and_the_plan_never_the_key(db, settings, capsys):
    respx.get(EVENTS).mock(return_value=httpx.Response(
        200, headers={"x-requests-remaining": "19870", "x-requests-last": "0"}, json=[]))
    assert cli.odds_status(db, client(), datetime(2026, 10, 15, 22, tzinfo=UTC)) == 0
    out = capsys.readouterr().out
    assert "key works" in out and "19,870" in out and "November 1" in out
    assert "two captures a game" in out and KEY not in out


@respx.mock
def test_a_refused_key_says_so_and_fails(db, settings, capsys):
    respx.get(EVENTS).mock(return_value=httpx.Response(401, json={"message": "Invalid key"}))
    assert cli.odds_status(db, client()) == 1
    out = capsys.readouterr().out
    assert "refused the key" in out and KEY not in out


def test_without_a_key_it_says_prices_are_off(db, settings, capsys):
    assert cli.odds_status(db) == 2
    assert "No Odds API key" in capsys.readouterr().out


def test_the_report_counts_and_names_but_never_shows_a_price(db, capsys):
    with Session(db) as s:
        game = seed_phi_chi_scenario(s, "final", datetime.now(tz=UTC))
        s.add(Lookup(path="none", text="what just happened", milliseconds=3, result="none:"))
        s.add(Lookup(path="deterministic", text="Barkley TD", milliseconds=3,
                     result=f"player:{game.id}"))
        s.commit()
    assert cli.report(db, date(2026, 9, 28)) == 0
    out = capsys.readouterr().out
    assert "PHI @ CHI" in out and "captures: t47" in out and "names matched" in out
    assert "Lookups since 2026-09-28: 2 (deterministic 1, none 1)" in out
    assert "'what just happened'" in out and "Barkley TD" not in out
    assert "-112" not in out  # Barkley's recorded price, and any other, stays out
