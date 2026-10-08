"""plays checked

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 15:49:57.650553
"""
from collections.abc import Sequence

from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Written by hand: autogenerate sees a rename as a drop and an add, which would lose the values.
# The column marks a game whose play-by-play has been read, which now drives more than the first
# touchdown: the exact deciding plays, the longest plays, and the late play-by-play retry.
def upgrade() -> None:
    op.alter_column('games', 'first_td_checked_at', new_column_name='plays_checked_at')


def downgrade() -> None:
    op.alter_column('games', 'plays_checked_at', new_column_name='first_td_checked_at')
