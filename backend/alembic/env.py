"""Alembic async env.

DATABASE_URL must be set in the environment (docker-compose injects it for the
migrate service).  The URL must use the asyncpg driver:
  postgresql+asyncpg://user:pass@host:port/dbname
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the URL from the environment so no credential appears in alembic.ini.
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. " "The migrate container must provide it."
    )
config.set_main_option("sqlalchemy.url", database_url)

# Import ORM metadata for autogenerate support (Phase 3+).
# For now we import the Base so it's available.
try:
    from app.repositories.orm_models import Base

    target_metadata = Base.metadata
except ImportError:
    target_metadata = None  # type: ignore[assignment]


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    connectable = create_async_engine(url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
