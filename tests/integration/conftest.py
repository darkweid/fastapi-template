# ruff: noqa: E402 -- TESTING must be set before any src.* import triggers config load
import os

os.environ.setdefault("TESTING", "true")

from collections.abc import AsyncGenerator
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.main.config import get_settings

INTEGRATION_ROOT = Path(__file__).resolve().parent
# tests/integration -> tests -> repo root, which is where alembic.ini lives and what
# `python -m alembic` must run from.
REPO_ROOT = INTEGRATION_ROOT.parents[1]


def pytest_itemcollected(item: pytest.Item) -> None:
    """Mark everything collected under tests/integration as `integration`.

    A module-level `pytestmark` would do the same, but forgetting it in one new file is
    enough to leak a database-dependent test into `make test`, which runs without Docker.
    Marking by location makes that impossible.

    `pytest_itemcollected` fires as each item is created, so the marker is in place before
    anything reads it — no assumption about how this hook orders against the `-m`
    deselection pass, which `pytest_collection_modifyitems` would need.
    """
    if item.path is not None and INTEGRATION_ROOT in item.path.resolve().parents:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def alembic_env() -> dict[str, str]:
    """Environment for an Alembic subprocess: the test database the fixtures use.

    `TESTING=true` selects `.env.test`, while POSTGRES_HOST/POSTGRES_PORT inherited
    from the caller (`make test-integration` or the CI job) override the file's values
    — the throwaway container's host port is only known at run time.
    """
    return {**os.environ, "TESTING": "true"}


@pytest.fixture(scope="session")
def migrated_database(alembic_env: dict[str, str]) -> None:
    """Apply the whole Alembic chain to a clean database once per session.

    The database is empty when a run starts — a throwaway container without a volume
    locally, a fresh service container in CI — so this both prepares the schema and
    proves the chain still applies from scratch.
    """
    get_settings.cache_clear()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
        env=alembic_env,
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def integration_engine(
    migrated_database: None,
) -> AsyncGenerator[AsyncEngine]:
    """Real engine against the test database, shared by the whole session.

    `loop_scope="session"` is not optional: an asyncpg connection cannot cross event
    loops, so a session-scoped engine has to be created on the session-scoped loop.
    Every test drawing on it therefore declares
    `pytestmark = pytest.mark.asyncio(loop_scope="session")` as well.

    Prepared-statement caching is off because the pool outlives DDL the suite performs
    on itself: a migration test can drop and recreate tables under connections that
    already cached plans against the old ones, and the resulting
    `InvalidCachedStatementError` would surface in whichever later test happens to draw
    that connection — far from the cause, and moving with collection order.
    """
    engine = create_async_engine(
        get_settings().postgres.dsn_async,
        connect_args={"statement_cache_size": 0},
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(integration_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Session whose work is rolled back afterwards, so tests do not see each other's rows."""
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()
