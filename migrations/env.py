from __future__ import annotations
import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from alembic import context

# Alembic Config object
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def _normalize(url: str) -> str:
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

# 1) Prefer DATABASE_URL from environment (Render style)
db_url = os.getenv("DATABASE_URL", "").strip()

target_metadata = None

# 2) If not set, fall back to Flask's config
if not db_url:
    from app import create_app
    from models import db
    flask_app = create_app()
    db_url = flask_app.config["SQLALCHEMY_DATABASE_URI"]
    target_metadata = db.metadata

# 3) If target_metadata still None (we didn't import models yet), import now
if target_metadata is None:
    from models import db
    target_metadata = db.metadata

db_url = _normalize(db_url)

# Ensure Alembic knows the URL (for offline mode)
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations without a DB connection."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,  # safer for SQLite
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    engine = create_engine(db_url, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
