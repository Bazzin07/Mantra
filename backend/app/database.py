import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .db_models import Base


def create_engine_and_session_factory(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    connect_args = {}
    if database_url.startswith("sqlite"):
        db_path = database_url.rsplit("///", 1)[-1]
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        connect_args = {"check_same_thread": False}

    engine = create_async_engine(database_url, echo=False, future=True, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
