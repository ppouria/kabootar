"""add message photos json payload

Revision ID: 0008_message_photos_json
Revises: 0007_channel_avatar_cache
Create Date: 2026-03-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_message_photos_json"
down_revision = "0007_channel_avatar_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("messages")}
    if "photos_json" not in cols:
        op.add_column("messages", sa.Column("photos_json", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("messages")}
    if "photos_json" in cols:
        op.drop_column("messages", "photos_json")
