"""add channel avatar cache columns

Revision ID: 0007_channel_avatar_cache
Revises: 0006_message_media_kind
Create Date: 2026-03-30
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_channel_avatar_cache"
down_revision = "0006_message_media_kind"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names("channels")
    if "avatar_mime" not in cols:
        op.add_column("channels", sa.Column("avatar_mime", sa.String(length=64), nullable=False, server_default=""))
    if "avatar_b64" not in cols:
        op.add_column("channels", sa.Column("avatar_b64", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    cols = _column_names("channels")
    if "avatar_b64" in cols:
        op.drop_column("channels", "avatar_b64")
    if "avatar_mime" in cols:
        op.drop_column("channels", "avatar_mime")
