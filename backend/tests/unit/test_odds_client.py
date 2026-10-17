"""The Odds API client's credit count (ingest/odds_api.py): it belongs to a key and a month, so an
old key's 0, or last month's, never stops the captures."""
from datetime import UTC, datetime

import httpx
import pytest
import respx

from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import OddsApiClient, key_fingerprint
from juju.ingest.router import Breakers

EVENTS = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events"
NOW = datetime(2026, 10, 15, 22, tzinfo=UTC)


def client(breakers: Breakers, key: str = "new-key", now: datetime = NOW,
           reset_day: int = 1) -> OddsApiClient:
    return OddsApiClient(key, breakers, RateLimiter(per_host_interval=0, per_minute=1000),
                         reset_day=reset_day, clock=lambda: now)


def seen(key: str | None, count: int, at: datetime | None = NOW) -> Breakers:
    """Breakers as a restart finds them: a count The Odds API reported for `key` at `at`."""
    breakers = Breakers(engine=None)
    b = breakers["odds_api"]
    b.quota_remaining, b.quota_at = count, at
    b.quota_key = None if key is None else key_fingerprint(key)
    return breakers


def test_a_fingerprint_names_a_key_without_containing_it():
    fp = key_fingerprint("a-long-secret-key-0123")
    assert len(fp) == 12 and int(fp, 16) >= 0
    assert fp == key_fingerprint("a-long-secret-key-0123") != key_fingerprint("another-key")
    assert fp not in "a-long-secret-key-0123" and "secret" not in fp


def test_a_new_key_never_inherits_another_keys_count():
    breakers = seen("old-key", 0)
    assert client(breakers).quota_remaining is None  # a full plan, until the first reply
    assert Breakers._row(breakers["odds_api"])["quota_remaining"] is None  # wiped when written
    assert Breakers._row(breakers["odds_api"])["quota_key"] == key_fingerprint("new-key")


def test_the_same_key_keeps_its_count():
    assert client(seen("new-key", 15_000)).quota_remaining == 15_000


def test_the_same_key_keeps_its_count_when_the_breakers_load_after_the_client():
    breakers = Breakers(engine=None)
    odds = client(breakers)
    stored = seen("new-key", 9_000)["odds_api"]
    b = breakers["odds_api"]  # what `load()` does
    b.quota_remaining, b.quota_key, b.quota_at = (stored.quota_remaining, stored.quota_key,
                                                  stored.quota_at)
    assert odds.quota_remaining == 9_000


def test_a_count_nobody_owns_is_unknown():
    assert client(seen(None, 500)).quota_remaining is None  # stored before migration 0009


@pytest.mark.parametrize("reset_day, before, after", [
    (1, datetime(2026, 10, 31, 23, tzinfo=UTC), datetime(2026, 11, 1, 0, 1, tzinfo=UTC)),
    (15, datetime(2026, 10, 14, 23, tzinfo=UTC), datetime(2026, 10, 15, 0, 1, tzinfo=UTC)),
])
def test_a_long_running_worker_stops_trusting_last_months_zero(reset_day, before, after):
    now = [before]
    breakers = seen("new-key", 0, before)
    odds = OddsApiClient("new-key", breakers, reset_day=reset_day, clock=lambda: now[0])
    assert odds.quota_remaining == 0
    now[0] = after  # the credits reset, and the free check never says
    assert odds.quota_remaining is None


@respx.mock
def test_a_reply_with_the_credits_header_records_them_for_this_key():
    respx.get(EVENTS).mock(return_value=httpx.Response(
        200, headers={"x-requests-remaining": "19870", "x-requests-last": "0"}, json=[]))
    breakers = seen("old-key", 0)
    odds = client(breakers)
    odds.events()
    assert (odds.quota_remaining, odds.quota_read_at, odds.quota_reported) == (19_870, NOW, True)
    assert breakers["odds_api"].quota_key == key_fingerprint("new-key")


@respx.mock
def test_a_reply_without_it_leaves_the_count_alone():
    respx.get(EVENTS).mock(return_value=httpx.Response(200, json=[]))
    earlier = datetime(2026, 10, 14, 18, tzinfo=UTC)
    odds = client(seen("new-key", 15_000, earlier))
    odds.events()
    assert (odds.quota_remaining, odds.quota_read_at, odds.quota_reported) == (
        15_000, earlier, False)
    assert odds.last_cost is None
