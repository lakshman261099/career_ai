"""force add is_public and meta_json to portfolio_page (Postgres-safe)"""

from alembic import op

revision = "a2626b1c1fd0"
down_revision = "20250826_add_project_table"  # <-- REPLACE with your current head id (add_project_table)
branch_labels = None
depends_on = None


def upgrade():
    # Add is_public if missing
    op.execute(
        """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='portfolio_page' AND column_name='is_public'
        ) THEN
            ALTER TABLE portfolio_page ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT FALSE;
        END IF;
    END$$;
    """
    )

    # Add meta_json if missing
    op.execute(
        """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='portfolio_page' AND column_name='meta_json'
        ) THEN
            ALTER TABLE portfolio_page ADD COLUMN meta_json JSONB DEFAULT '{}'::jsonb;
        END IF;
    END$$;
    """
    )


def downgrade():
    op.execute(
        """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='portfolio_page' AND column_name='meta_json'
        ) THEN
            ALTER TABLE portfolio_page DROP COLUMN meta_json;
        END IF;
    END$$;
    """
    )
    op.execute(
        """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='portfolio_page' AND column_name='is_public'
        ) THEN
            ALTER TABLE portfolio_page DROP COLUMN is_public;
        END IF;
    END$$;
    """
    )
