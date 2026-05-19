"""Async DB session dependency.

Extracted from dependencies.py so that infra/auth.py can import it without
creating a circular dependency (dependencies.py imports from infra/auth.py).
"""

from collections.abc import AsyncGenerator

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession for the current request; always close in finally."""
    engine = request.app.state.db_engine
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
