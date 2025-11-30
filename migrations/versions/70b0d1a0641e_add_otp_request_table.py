"""add otp_request table

Revision ID: 70b0d1a0641e
Revises: 20250905_add_text_to_resume_asset
Create Date: 2025-11-22 22:36:51.704269
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '70b0d1a0641e'
down_revision: Union[str, Sequence[str], None] = '20250905_add_text_to_resume_asset'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "otp_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_otp_request_email", "otp_request", ["email"])


def downgrade() -> None:
    # For SQLite, dropping the table removes its indexes as well.
    op.drop_table("otp_request")
