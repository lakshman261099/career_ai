"""add name to user

Revision ID: 3ed30037911f
Revises: 39217fc9ed69
Create Date: 2025-08-18 15:19:11.105752

"""
from typing import Sequence, Union

# migrations/versions/<hash>_add_name_to_user.py
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "<your_hash>"
down_revision = "<prev_revision_hash>"
branch_labels = None
depends_on = None

def upgrade():
    # "user" is a reserved word; Alembic/PG will auto-quote it when needed.
    op.add_column('user', sa.Column('name', sa.String(length=120), nullable=True))

def downgrade():
    op.drop_column('user', 'name')

