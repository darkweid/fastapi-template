# ruff: noqa: E402 -- TESTING must be set before any src.* import triggers config load
import os

os.environ.setdefault("TESTING", "true")

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
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


def _alembic_environment(**overrides: str) -> dict[str, str]:
    """Environment for an Alembic subprocess.

    `TESTING=true` selects `.env.test`, while POSTGRES_HOST/POSTGRES_PORT inherited
    from the caller (`make test-integration` or the CI job) override the file's values
    — the throwaway container's host port is only known at run time. A variable in
    `overrides` wins the same way, which is how a scratch database is named.
    """
    return {**os.environ, "TESTING": "true", **overrides}


@pytest.fixture(scope="session")
def alembic_env() -> dict[str, str]:
    """Alembic against the session's test database.

    `migrated_database` does not take it: a module running on a scratch database
    overrides this fixture at function scope, and a session fixture depending on it
    would then fail with ScopeMismatch.
    """
    return _alembic_environment()


@pytest.fixture(scope="session")
def migrated_database() -> None:
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
        env=_alembic_environment(),
    )


async def _run_on_server(statement: str) -> None:
    """CREATE/DROP DATABASE cannot run inside a transaction, nor while anyone is
    connected to the database it copies or drops, so it goes through a maintenance
    database with autocommit: `postgres`, or `template1` when `postgres` is itself
    the test database being copied."""
    source = get_settings().postgres.POSTGRES_DB
    maintenance = "template1" if source == "postgres" else "postgres"
    url = make_url(get_settings().postgres.dsn_async).set(database=maintenance)
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database_template(migrated_database: None) -> AsyncGenerator[str]:
    """A copy of the test database as the migrations leave it, before any test writes
    a row: what `scratch_database` clones.

    Taken here, ahead of `integration_engine`, because PostgreSQL refuses to copy a
    database anyone is connected to. The name is generated rather than derived from
    the source: a derived one past the 63-byte identifier limit is truncated, and
    could come back as the source itself.
    """
    source = get_settings().postgres.POSTGRES_DB
    name = f"template_{uuid4().hex}"
    await _run_on_server(f'CREATE DATABASE "{name}" TEMPLATE "{source}"')
    try:
        yield name
    finally:
        await _run_on_server(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@dataclass(frozen=True)
class ScratchDatabase:
    engine: AsyncEngine
    alembic_env: dict[str, str]


@pytest_asyncio.fixture(loop_scope="session")
async def scratch_database(
    database_template: str,
) -> AsyncGenerator[ScratchDatabase]:
    """A database of the test's own, freshly migrated and holding no test's rows,
    dropped afterwards.

    For the few tests that cannot share the session database: a migration run
    backwards and forwards over whatever the other tests left behind, or a data
    migration asserting what an untouched database ends up with. Every other test
    cleans up after itself instead - this is not a way around that. A test takes it
    directly, or its module overrides `integration_engine` (and `alembic_env`, when
    it runs Alembic) with this fixture's fields, so `db_session` and every fixture
    built on the engine follow it there.
    """
    name = f"scratch_{uuid4().hex}"
    await _run_on_server(f'CREATE DATABASE "{name}" TEMPLATE "{database_template}"')
    engine = create_async_engine(
        make_url(get_settings().postgres.dsn_async).set(database=name),
        connect_args={"statement_cache_size": 0},
    )
    try:
        yield ScratchDatabase(
            engine=engine,
            alembic_env=_alembic_environment(POSTGRES_DB=name),
        )
    finally:
        await engine.dispose()
        await _run_on_server(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def integration_engine(
    database_template: str,
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
