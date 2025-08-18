from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# import your app + db
from app import app, db

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
fileConfig(config.config_file_name)

target_metadata = db.metadata


def run_migrations_online():
    # Use Flask app context so db.engine is available
    with app.app_context():
        connectable = db.engine

        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,  # detect column type changes
            )

            with context.begin_transaction():
                context.run_migrations()


run_migrations_online()
