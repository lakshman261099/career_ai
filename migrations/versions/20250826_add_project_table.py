"""add project table"""

import sqlalchemy as sa
from alembic import op

# --- Alembic revision identifiers ---
# Use the hex prefix from the filename for `revision`
# Example: if the filename is "20250826_1234_add_project_table.py",
# set revision = "20250826_1234"
revision = "20250826_add_project_table"
down_revision = "131d3d348caa"  # points to add_user_profile_table
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("short_desc", sa.String(length=500), nullable=True),
        sa.Column("bullets", sa.JSON(), nullable=True, server_default=sa.text("'[]'")),
        sa.Column(
            "tech_stack", sa.JSON(), nullable=True, server_default=sa.text("'[]'")
        ),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=True, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_project_user", "project", ["user_id"])


def downgrade():
    op.drop_index("ix_project_user", table_name="project")
    op.drop_table("project")
