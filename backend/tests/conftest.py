"""Suite-wide rules: no test reaches the network, DB tests need TEST_DATABASE_URL, and the test
database is built by the Alembic migrations (as in parlaytracker)."""
import os
import socket
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from juju.config import normalize_database_url

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

_real_connect = socket.socket.connect
_LOCAL = ("127.0.0.1", "::1", "localhost")


def _guarded_connect(self, address):
    """Only local connections (the test Postgres): a test that tries the internet fails."""
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and not (host in _LOCAL or host.startswith("/")):
        raise RuntimeError(f"tests must not use the network (tried {host})")
    return _real_connect(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    has_db = bool(os.environ.get("TEST_DATABASE_URL"))
    if not has_db and os.environ.get("CI"):
        raise pytest.UsageError("TEST_DATABASE_URL must be set in CI")
    for item in items:
        if "db" in item.keywords and not has_db:
            item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set (see README)"))


def alembic_config(connection: Connection) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture(scope="session")
def engine() -> Engine:
    url = normalize_database_url(os.environ["TEST_DATABASE_URL"])
    database = make_url(url).database or ""
    if "test" not in database:
        pytest.exit(f"refusing to wipe database {database!r}: its name must contain 'test'", 2)
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        command.upgrade(alembic_config(conn), "head")
    yield eng
    eng.dispose()
