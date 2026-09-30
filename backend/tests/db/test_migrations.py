"""The migrations build exactly the schema the models describe."""
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from juju.core.models import Base

pytestmark = pytest.mark.db


def test_migrated_schema_matches_the_models(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []
