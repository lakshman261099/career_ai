"""add text column to resume_asset (dual: SQLite + Postgres, idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers
revision = "20250905_add_text_to_resume_asset"
down_revision = "a2626b1c1fd0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Get existing column names
    columns = [col["name"] for col in inspector.get_columns("resume_asset")]

    # Only add column if missing
    if "text" not in columns:
        with op.batch_alter_table("resume_asset") as batch_op:
            batch_op.add_column(sa.Column("text", sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    columns = [col["name"] for col in inspector.get_columns("resume_asset")]

    if "text" in columns:
        with op.batch_alter_table("resume_asset") as batch_op:
            batch_op.drop_column("text")
