"""force add is_public and meta_json to portfolio_page (dual: Postgres + SQLite)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "a2626b1c1fd0"
down_revision = "20250826_add_project_table"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name.lower()
    inspector = inspect(bind)

    # Get existing columns on portfolio_page
    columns = [col["name"] for col in inspector.get_columns("portfolio_page")]

    # Add is_public if missing
    if "is_public" not in columns:
        op.add_column(
            "portfolio_page",
            sa.Column(
                "is_public",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0")  # SQLite & Postgres compatible enough
            ),
        )

    # Add meta_json if missing
    if "meta_json" not in columns:
        op.add_column(
            "portfolio_page",
            sa.Column(
                "meta_json",
                sa.JSON(),  # In SQLite this will be TEXT, in Postgres proper JSON
                nullable=True,
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("portfolio_page")]

    if "meta_json" in columns:
        op.drop_column("portfolio_page", "meta_json")

    if "is_public" in columns:
        op.drop_column("portfolio_page", "is_public")
