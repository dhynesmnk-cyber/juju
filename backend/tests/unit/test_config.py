# Ported from parlaytracker@c3bd43c tests/unit/test_config_and_markets.py: its settings tests.
# Changes: Juju's settings (a reserve of 1000, BOOKS; DISPLAY_TZ has a default, US Eastern, where
# parlaytracker required it), and TRACKER_USERS, which keeps names and drops passcodes.
"""Settings: the only place environment variables are read, and keys never reach an error."""
import pytest
from pydantic import ValidationError

from juju.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_normalize_database_url(url, expected):
    assert normalize_database_url(url) == expected


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    for name in ("ODDS_API_KEY", "LLM_API_KEY", "BOOKS", "RECORD_EVENT_IDS", "ODDS_API_RESERVE",
                 "TRACKER_USERS", "DISPLAY_TZ", "MIN_SAMPLE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_from_env(env):
    env.setenv("RECORD_EVENT_IDS", "401, 402,,")
    env.setenv("ODDS_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://u:p@h/db"
    assert s.record_event_ids == ["401", "402"]
    assert s.odds_api_key is None  # empty counts as unset
    assert s.odds_api_reserve == 1000
    assert s.books[:2] == ["hardrockbet", "draftkings"]
    assert (s.display_tz, s.min_sample, s.tracker_users) == ("America/New_York", 30, [])


def test_nothing_is_recorded_unless_asked(env):
    assert Settings(_env_file=None).record_event_ids == []


def test_api_keys_never_appear_in_errors_or_repr(env):
    secret = "fcsecretkey0123456789abcdefsecret"
    env.setenv("ODDS_API_KEY", secret)
    env.setenv("LLM_API_KEY", secret)
    settings = Settings(_env_file=None)
    assert settings.odds_api_key.get_secret_value() == secret
    assert secret not in repr(settings) and secret not in str(settings)
    env.setenv("BOOKS", ",".join(f"book{n}" for n in range(11)))  # more than 10: invalid
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None)
    assert secret not in str(info.value)
    assert secret[:7] not in str(info.value)


def test_display_tz_must_be_a_real_zone(env):
    env.setenv("DISPLAY_TZ", "Europe/London")
    assert Settings(_env_file=None).display_tz == "Europe/London"
    env.setenv("DISPLAY_TZ", "Mars/Olympus")
    with pytest.raises(ValidationError, match="IANA"):
        Settings(_env_file=None)


def test_tracker_users_keep_the_names_and_never_the_passcodes(env):
    passcode = "lantern-quartz-5512"
    env.setenv("TRACKER_USERS", f" Chris:{passcode} , Ash:with:colons-4471,,")
    settings = Settings(_env_file=None)
    assert settings.tracker_users == ["Chris", "Ash"]
    assert passcode not in repr(settings) and "colons" not in repr(settings)
    env.setenv("TRACKER_USERS", "Chris,Ash")  # names alone: up.sh fills in the passcodes
    assert Settings(_env_file=None).tracker_users == ["Chris", "Ash"]


@pytest.mark.parametrize("value", ["Chris:one-long-passcode,chris:two-long-passcode",
                                   "chris.b:one-long-passcode", "9ers:one-long-passcode",
                                   ":one-long-passcode", "Chris one-long-passcode"])
def test_a_bad_or_repeated_tracker_name_is_refused_without_echoing_a_passcode(env, value):
    env.setenv("TRACKER_USERS", value)
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None)
    assert "long-passcode" not in str(info.value)
