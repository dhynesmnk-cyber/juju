# Ported from parlaytracker@c3bd43c tests/app/test_app.py: its health-banner cases. Changes: they
# call the pure `health_banner` on rows built in memory, not the Streamlit page on a database; and
# the reserve is an argument (parlaytracker's tests ran with 50).
"""The tracker's health banner (SPEC.md section 9.2)."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from juju.core.enums import FailureKind, HealthState
from juju.core.models import SourceHealth
from juju.tracker.banner import health_banner

NOW = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
TZ = ZoneInfo("America/New_York")


def text(**rows) -> str:
    """The banner's messages for sources given as name=dict(SourceHealth columns)."""
    health = [SourceHealth(source=source, **values) for source, values in rows.items()]
    return " ".join(b.text for b in health_banner(health, NOW, 50, TZ))


def levels(**rows) -> list[str]:
    health = [SourceHealth(source=source, **values) for source, values in rows.items()]
    return [b.level for b in health_banner(health, NOW, 50, TZ)]


def test_banner_is_quiet_when_the_worker_and_sources_are_healthy():
    assert text(worker=dict(state=HealthState.OK, last_success_at=NOW),
                odds_api=dict(state=HealthState.OK, last_success_at=NOW,
                              quota_remaining=480)) == ""


def test_banner_warns_when_the_worker_has_never_run():
    assert "never run" in text()


def test_banner_warns_when_the_worker_has_stopped():
    out = text(worker=dict(state=HealthState.OK, last_success_at=NOW - timedelta(minutes=10)))
    assert "last seen Sun 04 Oct 13:50" in out  # in the display time zone


def test_banner_warns_when_credits_run_low():
    out = text(worker=dict(state=HealthState.OK, last_success_at=NOW),
               odds_api=dict(state=HealthState.OK, quota_remaining=57))
    assert "57 credits" in out and "50" in out


def test_banner_names_a_source_that_has_been_failing():
    out = text(worker=dict(state=HealthState.OK, last_success_at=NOW),
               odds_api=dict(state=HealthState.OPEN, failure_kind=FailureKind.BLOCKED,
                             open_until=NOW + timedelta(minutes=5),
                             last_success_at=NOW - timedelta(hours=1)))
    assert "odds_api is failing (blocked)" in out


def test_banner_says_when_the_data_format_changed():
    rows = dict(worker=dict(state=HealthState.OK, last_success_at=NOW),
                odds_api=dict(state=HealthState.OPEN, failure_kind=FailureKind.SCHEMA,
                              open_until=NOW + timedelta(minutes=30)))
    assert "the data format changed" in text(**rows)
    assert levels(**rows) == ["error"]


def test_banner_does_not_warn_about_a_provider_whose_open_time_has_passed():
    assert text(worker=dict(state=HealthState.OK, last_success_at=NOW),
                espn_site=dict(state=HealthState.OPEN, failure_kind=FailureKind.BLOCKED,
                               open_until=NOW - timedelta(minutes=1),
                               last_success_at=NOW - timedelta(hours=2))) == ""


def test_banner_still_warns_about_a_schema_failure_after_its_open_time_passes():
    assert "the data format changed" in text(
        worker=dict(state=HealthState.OK, last_success_at=NOW),
        espn_web=dict(state=HealthState.OPEN, failure_kind=FailureKind.SCHEMA,
                      open_until=NOW - timedelta(minutes=1)))
