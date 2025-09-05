# migrations/env.py

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object
config = context.config

# Logging config from alembic.ini (optional)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models metadata directly (no app import; avoids recursion)
from models import db
target_metadata = db.metadata


def _db_url():
    """
    Decide which database URL to use:
    1. If Alembic is run with -x dburl=..., prefer that (supports ?sslmode=require).
    2. Else, use DATABASE_URL from environment.
    3. Else, fallback to alembic.ini sqlalchemy.url.
    4. Else, fallback to local sqlite.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "dburl" in x_args:
        url = x_args["dburl"]
    else:
        url = (
            os.getenv("DATABASE_URL")
            or config.get_main_option("sqlalchemy.url")
            or "sqlite:///career_ai.db"
        )

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def run_migrations_offline():
    url = _db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=url.startswith("sqlite"),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = _db_url()
    config.set_main_option("sqlalchemy.url", url)

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
            render_as_batch=url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
