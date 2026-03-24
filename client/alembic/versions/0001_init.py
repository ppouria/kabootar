"""init

Revision ID: 0001_init
Revises: 
Create Date: 2026-03-17
"""
import sqlalchemy as sa
from alembic import op

revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'channels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('source_url', sa.String(length=1024), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_channels_username', 'channels', ['username'], unique=True)

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('channel_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('published_at', sa.String(length=128), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('has_media', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('channel_id', 'message_id', name='uq_channel_message'),
    )
    op.create_index('ix_messages_channel_id', 'messages', ['channel_id'], unique=False)
    op.create_index('ix_messages_message_id', 'messages', ['message_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_messages_message_id', table_name='messages')
    op.drop_index('ix_messages_channel_id', table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_channels_username', table_name='channels')
    op.drop_table('channels')
