"""add text column to resume_asset

Revision ID: 20250905_add_text_to_resume_asset
Revises: a2626b1c1fd0
Create Date: 2025-09-05 21:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250905_add_text_to_resume_asset"
down_revision = "a2626b1c1fd0"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("resume_asset") as batch_op:
        batch_op.add_column(sa.Column("text", sa.Text(), nullable=True))

def downgrade():
    with op.batch_alter_table("resume_asset") as batch_op:
        batch_op.drop_column("text")
