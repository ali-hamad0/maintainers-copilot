from collections.abc import AsyncGenerator

import structlog
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


def _get_session_maker(request: Request) -> async_sessionmaker[AsyncSession]:
    engine = request.app.state.db_engine
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(
    session_maker: async_sessionmaker[AsyncSession] = Depends(_get_session_maker),
) -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
