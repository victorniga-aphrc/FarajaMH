from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# ✅ Import your SQLAlchemy models and DB URL
from models import Base, DB_URL

# This is the Alembic Config object, which provides access to values within the .ini file.
config = context.config

# 🧩 Set the connection string dynamically (from your models.py)
config.set_main_option("sqlalchemy.url", DB_URL)

# Interpret the config file for Python logging.
fileConfig(config.config_file_name)

# Add your model's MetaData object for 'autogenerate' support.
target_metadata = Base.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
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
        )

        with context.begin_transaction():
            context.run_migrations()


# Entry point
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
