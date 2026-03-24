"""add channel meta

Revision ID: 0002_channel_meta
Revises: 0001_init
Create Date: 2026-03-17
"""
import sqlalchemy as sa
from alembic import op

revision = '0002_channel_meta'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql("PRAGMA table_info(channels)").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names()
    if 'title' not in cols:
      op.add_column('channels', sa.Column('title', sa.String(length=255), nullable=False, server_default=''))
    if 'avatar_url' not in cols:
      op.add_column('channels', sa.Column('avatar_url', sa.String(length=1024), nullable=False, server_default=''))


def downgrade() -> None:
    cols = _column_names()
    if 'avatar_url' in cols:
      op.drop_column('channels', 'avatar_url')
    if 'title' in cols:
      op.drop_column('channels', 'title')
