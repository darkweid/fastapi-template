"""The Alembic chain against a real, empty database.

Only a live PostgreSQL can answer these: whether every revision applies from scratch, and
whether the models and the migrations still describe the same schema.
"""

import asyncio
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import REPO_ROOT

pytestmark = pytest.mark.asyncio(loop_scope="session")

EXPECTED_TABLES = {"users", "notes", "outbox_messages"}


async def test_chain_applies_to_a_clean_database(
    integration_engine: AsyncEngine,
) -> None:
    """`migrated_database` ran `alembic upgrade head` against an empty database.

    The container carries no volume, so the run really did start from nothing; a
    non-zero exit would already have failed the fixture. What is left to check is that
    the chain ended on a single revision and produced the tables the models declare.
    """
    async with integration_engine.connect() as connection:
        revisions = (
            (await connection.execute(text("SELECT version_num FROM alembic_version")))
            .scalars()
            .all()
        )
        tables = (
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )

    assert len(revisions) == 1
    assert EXPECTED_TABLES <= set(tables)


async def test_models_and_migrations_do_not_drift(
    migrated_database: None, alembic_env: dict[str, str]
) -> None:
    """`alembic check` must find nothing to autogenerate.

    This is the test that catches a model edited without a migration: the diff exists
    only against a database that has the whole chain applied, so no fake can stand in.
    """
    # to_thread, not a bare call: `subprocess.run` blocks, and the engine fixture shares
    # this event loop with every other test in the session.
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=alembic_env,
    )

    assert result.returncode == 0, (
        "Models and migrations have drifted apart; run `make migration`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
