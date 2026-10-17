"""`odds-status` and `report` (juju/cli.py): what the owner runs on the laptop."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from juju import cli
from juju.config import get_settings
from juju.core.models import Lookup
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import OddsApiClient, key_fingerprint
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


NOW = datetime(2026, 10, 15, 22, tzinfo=UTC)


def client(breakers: Breakers | None = None) -> OddsApiClient:
    return OddsApiClient(KEY, breakers or Breakers(engine=None),
                         RateLimiter(per_host_interval=0, per_minute=1000), clock=lambda: NOW)


def seen(key: str, count: int, at: datetime) -> Breakers:
    """Breakers holding a count The Odds API reported for `key` at `at`, as the worker left it."""
    breakers = Breakers(engine=None)
    breakers.hold_key("odds_api", key_fingerprint(key))
    breakers.success("odds_api", quota_remaining=count, quota_at=at)
    return breakers


def no_secrets(out: str) -> bool:
    return KEY not in out and key_fingerprint(KEY) not in out


@respx.mock
def test_a_working_key_shows_the_credits_and_the_plan_never_the_key(db, settings, capsys):
    respx.get(EVENTS).mock(return_value=httpx.Response(
        200, headers={"x-requests-remaining": "19870", "x-requests-last": "0"}, json=[]))
    assert cli.odds_status(db, client(), NOW) == 0
    out = capsys.readouterr().out
    assert "key works" in out and "19,870 (The Odds API, just now)" in out
    assert "November 1" in out and "two captures a game" in out and no_secrets(out)


@respx.mock
def test_a_free_check_without_credits_says_when_juju_last_saw_them(db, settings, capsys):
    respx.get(EVENTS).mock(return_value=httpx.Response(200, json=[]))  # no credits header
    breakers = seen(KEY, 15_000, datetime(2026, 10, 14, 18, tzinfo=UTC))
    assert cli.odds_status(db, client(breakers), NOW) == 0
    out = capsys.readouterr().out
    assert "15,000 when Juju last saw them, on October 14 at 18:00 UTC" in out
    assert "doesn't report" in out and "two captures a game" in out and no_secrets(out)


@respx.mock
@pytest.mark.parametrize("key, read", [
    ("an-older-key", NOW - timedelta(days=1)),  # .env had the free key until today
    (KEY, datetime(2026, 9, 30, tzinfo=UTC)),   # this key, but last month's count
])
def test_an_old_zero_never_stops_the_captures(db, settings, capsys, key, read):
    respx.get(EVENTS).mock(return_value=httpx.Response(200, json=[]))
    assert cli.odds_status(db, client(seen(key, 0, read)), NOW) == 0
    out = capsys.readouterr().out
    assert "not known until the first capture" in out and "Captures: none" not in out
    assert no_secrets(out)


@respx.mock
def test_this_months_zero_says_captures_none_for_up_sh(db, settings, capsys):
    respx.get(EVENTS).mock(return_value=httpx.Response(200, json=[]))
    assert cli.odds_status(db, client(seen(KEY, 0, NOW - timedelta(days=1))), NOW) == 0
    out = capsys.readouterr().out
    assert "Captures: none" in out and no_secrets(out)  # deploy/laptop/up.sh looks for this


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
