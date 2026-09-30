import pytest
from sqlalchemy import Engine, text

from juju.core.models import Base


@pytest.fixture
def db(engine: Engine):
    """The migrated test database, emptied after each test."""
    yield engine
    with engine.begin() as conn:
        names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
