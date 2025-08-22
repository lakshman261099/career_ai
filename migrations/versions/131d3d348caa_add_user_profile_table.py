"""add user_profile table (minimal)

Revision ID: 131d3d348caa
Revises: ff450f3ae804
Create Date: 2025-08-22
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "131d3d348caa"
down_revision = "ff450f3ae804"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("headline", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("links", sa.JSON(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("education", sa.JSON(), nullable=True),
        sa.Column("experience", sa.JSON(), nullable=True),
        sa.Column("certifications", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Helpful explicit index (unique already implies an index, this is optional)
    op.create_index("ix_user_profile_user_id", "user_profile", ["user_id"], unique=True)


def downgrade():
    op.drop_index("ix_user_profile_user_id", table_name="user_profile")
    op.drop_table("user_profile")
