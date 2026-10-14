"""conversation summary columns and the settings table

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text()))
    op.add_column("conversations", sa.Column("summary_checkpoint_message_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "conversations", sa.Column("recent_token_estimate", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),  # a single row, id = 1
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_column("conversations", "recent_token_estimate")
    op.drop_column("conversations", "summary_checkpoint_message_id")
    op.drop_column("conversations", "summary")
