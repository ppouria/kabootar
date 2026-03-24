"""add message reply metadata

Revision ID: 0003_message_reply_meta
Revises: 0002_channel_meta
Create Date: 2026-03-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_message_reply_meta"
down_revision = "0002_channel_meta"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names("messages")
    if "reply_to_message_id" not in cols:
        op.add_column("messages", sa.Column("reply_to_message_id", sa.Integer(), nullable=True))
    if "reply_author" not in cols:
        op.add_column("messages", sa.Column("reply_author", sa.String(length=255), nullable=False, server_default=""))
    if "reply_text" not in cols:
        op.add_column("messages", sa.Column("reply_text", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    cols = _column_names("messages")
    if "reply_text" in cols:
        op.drop_column("messages", "reply_text")
    if "reply_author" in cols:
        op.drop_column("messages", "reply_author")
    if "reply_to_message_id" in cols:
        op.drop_column("messages", "reply_to_message_id")
