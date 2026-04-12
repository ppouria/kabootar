"""add message media kind

Revision ID: 0006_message_media_kind
Revises: 0005_message_forward_source
Create Date: 2026-03-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_message_media_kind"
down_revision = "0005_message_forward_source"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names("messages")
    if "media_kind" not in cols:
        op.add_column("messages", sa.Column("media_kind", sa.String(length=32), nullable=False, server_default=""))


def downgrade() -> None:
    cols = _column_names("messages")
    if "media_kind" in cols:
        op.drop_column("messages", "media_kind")
