"""baseline

Empty first migration: establishes the alembic_version table so the migration
pipeline is proven end to end. Real tables start in Phase 1 (projects).

Revision ID: 0001
Revises:
Create Date: 2026-10-09
"""
from alembic import op  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
