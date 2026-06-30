"""Initial schema — all 5 tables for MeetMind Phase 1.

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-06-30 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- users ----
    op.create_table(
        'users',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # ---- meetings ----
    op.create_table(
        'meetings',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('meeting_datetime', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('external_meeting_id', sa.String(255), nullable=True),
        sa.Column('raw_transcript', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint(
            "source IN ('teams_live','teams_export','meet_export','manual')",
            name='ck_meetings_source',
        ),
        sa.CheckConstraint(
            "status IN ('draft','awaiting_join','recording','processing','ready','failed')",
            name='ck_meetings_status',
        ),
    )
    op.create_index('ix_meetings_external_meeting_id', 'meetings', ['external_meeting_id'])

    # ---- meeting_attendees ----
    op.create_table(
        'meeting_attendees',
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.PrimaryKeyConstraint('meeting_id', 'user_id'),
    )
    op.create_index('ix_meeting_attendees_user_id', 'meeting_attendees', ['user_id'])

    # ---- chat_threads ----
    op.create_table(
        'chat_threads',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('user_id', 'meeting_id', name='uq_chat_threads_user_meeting'),
    )

    # ---- chat_messages ----
    op.create_table(
        'chat_messages',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('thread_id', UUID(as_uuid=True), sa.ForeignKey('chat_threads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(10), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("role IN ('user','assistant')", name='ck_chat_messages_role'),
    )
    op.create_index('ix_chat_messages_thread_created', 'chat_messages', ['thread_id', 'created_at'])


def downgrade() -> None:
    op.drop_table('chat_messages')
    op.drop_table('chat_threads')
    op.drop_table('meeting_attendees')
    op.drop_table('meetings')
    op.drop_table('users')
