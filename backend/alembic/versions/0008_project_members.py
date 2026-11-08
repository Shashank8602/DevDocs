"""per-project members

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
    # People who were named on a rule before membership existed keep their access: make them members.
    op.execute(
        """
        INSERT INTO project_members (id, project_id, user_id)
        SELECT gen_random_uuid(), r.project_id, u.id
        FROM access_rules r
        CROSS JOIN LATERAL jsonb_array_elements_text(r.user_ids) AS ref(uid)
        JOIN users u ON u.id::text = ref.uid
        GROUP BY r.project_id, u.id
        ON CONFLICT DO NOTHING
        """
    )
    # A project with an "everyone" rule used to be readable by every account: keep that for existing people.
    op.execute(
        """
        INSERT INTO project_members (id, project_id, user_id)
        SELECT gen_random_uuid(), p.project_id, u.id
        FROM (SELECT DISTINCT project_id FROM access_rules WHERE visibility = 'everyone') p
        CROSS JOIN users u
        WHERE u.role = 'member'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("project_members")
