"""chunks table

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("page_number", sa.Integer()),
        sa.Column("section", sa.Text()),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("file_id", "ordinal", name="uq_chunks_file_ordinal"),
    )
    op.create_index("ix_chunks_project_id", "chunks", ["project_id"])
    op.create_index("ix_chunks_file_id", "chunks", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_file_id", table_name="chunks")
    op.drop_index("ix_chunks_project_id", table_name="chunks")
    op.drop_table("chunks")
