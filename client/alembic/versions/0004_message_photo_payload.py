"""add message photo payload

Revision ID: 0004_message_photo_payload
Revises: 0003_message_reply_meta
Create Date: 2026-03-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_message_photo_payload"
down_revision = "0003_message_reply_meta"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names("messages")
    if "photo_mime" not in cols:
        op.add_column("messages", sa.Column("photo_mime", sa.String(length=64), nullable=False, server_default=""))
    if "photo_b64" not in cols:
        op.add_column("messages", sa.Column("photo_b64", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    cols = _column_names("messages")
    if "photo_b64" in cols:
        op.drop_column("messages", "photo_b64")
    if "photo_mime" in cols:
        op.drop_column("messages", "photo_mime")
