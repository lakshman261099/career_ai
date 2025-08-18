# migrations/env.py

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Load Flask app & metadata ----------------------------
# Ensure project root is importable
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from models import db  # db.metadata is our target metadata

flask_app = create_app()

with flask_app.app_context():
    url = flask_app.config.get("SQLALCHEMY_DATABASE_URI")
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # Override alembic.ini sqlalchemy.url using Flask config
    config.set_main_option("sqlalchemy.url", url or "sqlite:///career_ai.db")

target_metadata = db.metadata

# SQLite needs "render_as_batch" for many ALTER operations
is_sqlite = (config.get_main_option("sqlalchemy.url") or "").startswith("sqlite")

# ----------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
