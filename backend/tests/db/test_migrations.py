"""The migrations build exactly the schema the models describe.

Alembic's own comparison sees tables, columns, types, indexes, unique constraints and foreign
keys, but not CHECK constraints, server defaults or seeded rows, and the tracker's integrity lives
in its CHECKs. So the migrated schema is also compared with one `create_all` builds, catalog to
catalog (after parlaytracker@c3bd43c tests/db/test_migrations.py).
"""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, select, text

from juju.core.models import Base
from juju.tracker.models import Sportsbook
from tests.conftest import alembic_config

pytestmark = pytest.mark.db

MODEL_SCHEMA = "model_check"
TRACKER_TABLES = {"sportsbooks", "events", "slips", "legs", "tags", "leg_tags", "raw_samples"}


def test_migrated_schema_matches_the_models(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def _catalog(conn: Connection, schema: str) -> dict[str, set]:
    """Columns, constraints and indexes of a schema, with the schema name stripped out."""
    params = {"s": schema}
    columns = conn.execute(text("""
        SELECT table_name, column_name, data_type, is_nullable, column_default,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name <> 'alembic_version'
    """), params)
    constraints = conn.execute(text("""
        SELECT c.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s AND c.relname <> 'alembic_version'
    """), params)
    indexes = conn.execute(text("""
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = :s AND tablename <> 'alembic_version'
    """), params)

    def strip(row):
        return tuple(str(v).replace(f"{schema}.", "") if v is not None else None for v in row)

    return {
        "columns": {strip(r) for r in columns},
        "constraints": {strip(r) for r in constraints},
        "indexes": {strip(r) for r in indexes},
    }


def test_constraints_defaults_and_indexes_match_the_models(engine):
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {MODEL_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {MODEL_SCHEMA}"))
        Base.metadata.create_all(conn.execution_options(schema_translate_map={None: MODEL_SCHEMA}))
        migrated, modelled = _catalog(conn, "public"), _catalog(conn, MODEL_SCHEMA)
        conn.execute(text(f"DROP SCHEMA {MODEL_SCHEMA} CASCADE"))
    for kind in ("columns", "constraints", "indexes"):
        assert migrated[kind] == modelled[kind], kind


def test_the_tracker_migration_goes_down_and_up_again(engine):
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "0005")
        tables = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())
        assert not tables & TRACKER_TABLES
        assert {"games", "prices", "source_health"} <= tables  # Juju's own are untouched
        command.upgrade(alembic_config(conn), "head")
        books = conn.execute(select(Sportsbook.name, Sportsbook.odds_api_key)).all()
    assert sorted(books) == [
        ("BetMGM", "betmgm"),
        ("Caesars", "williamhill_us"),
        ("DraftKings", "draftkings"),
        ("FanDuel", "fanduel"),
    ]
