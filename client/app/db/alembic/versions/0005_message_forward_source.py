"""add message forward source

Revision ID: 0005_message_forward_source
Revises: 0004_message_photo_payload
Create Date: 2026-03-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_message_forward_source"
down_revision = "0004_message_photo_payload"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    cols = _column_names("messages")
    if "forward_source" not in cols:
        op.add_column("messages", sa.Column("forward_source", sa.String(length=255), nullable=False, server_default=""))


def downgrade() -> None:
    cols = _column_names("messages")
    if "forward_source" in cols:
        op.drop_column("messages", "forward_source")
