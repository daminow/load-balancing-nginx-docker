from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres-test-pwd")
os.environ.setdefault("POSTGRES_DB", "appdb_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("INSTANCE_ID", "test-instance")

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture()
async def engine(settings):  # type: ignore[no-untyped-def]
    eng = make_engine(settings)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[no-untyped-def]
    return make_sessionmaker(engine)


@pytest_asyncio.fixture()
async def client(session_factory, settings) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    app = create_app(settings)
    app.state.session_factory = session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
