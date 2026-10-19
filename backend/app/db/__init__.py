"""Database configuration and session management."""

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.postgres import engine

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session, committed when the endpoint returns.

    Routes declare it with ``Depends(get_session, scope="function")`` so the commit runs before
    the response is sent. With the default request scope the client received 200 before the
    commit: a reader could miss what was just written, and a failed commit lost an acknowledged
    batch that the exporter would never retry (found in the 2026-10-19 migration rehearsal on
    PostgreSQL)."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# What routes declare: ``session: AsyncSession = DbSession``.
DbSession = Depends(get_session, scope="function")
