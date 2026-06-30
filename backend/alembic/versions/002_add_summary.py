"""Add summary column and update status constraint.

Revision ID: 002_add_summary
Revises: 001_initial_schema
Create Date: 2026-06-30 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002_add_summary'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the summary column
    op.add_column('meetings', sa.Column('summary', sa.Text(), nullable=True))

    # Drop the old status check constraint and add the new one with 'summarizing'
    op.drop_constraint('ck_meetings_status', 'meetings', type_='check')
    op.create_check_constraint(
        'ck_meetings_status',
        'meetings',
        "status IN ('draft','awaiting_join','recording','processing','summarizing','ready','failed')",
    )


def downgrade() -> None:
    # Revert the status check constraint
    op.drop_constraint('ck_meetings_status', 'meetings', type_='check')
    op.create_check_constraint(
        'ck_meetings_status',
        'meetings',
        "status IN ('draft','awaiting_join','recording','processing','ready','failed')",
    )

    # Drop the summary column
    op.drop_column('meetings', 'summary')
