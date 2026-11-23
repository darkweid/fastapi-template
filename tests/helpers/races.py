"""Forcing an interleaving: one side waits at a known point, or on a row lock
PostgreSQL itself reports."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

WAIT_SECONDS = 10


class Pause:
    """Wraps a coroutine function: signals arrival, then waits for release."""

    def __init__(self, target: Callable[..., Awaitable[Any]], *, after: bool) -> None:
        self._target = target
        self._after = after
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._target(*args, **kwargs) if self._after else None
        self.reached.set()
        await asyncio.wait_for(self.release.wait(), WAIT_SECONDS)
        return result if self._after else await self._target(*args, **kwargs)


def pause(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, *, after: bool
) -> Pause:
    paused = Pause(getattr(module, name), after=after)
    monkeypatch.setattr(module, name, paused)
    return paused


async def wait_for_lock_wait(engine: AsyncEngine, table: str) -> None:
    """Returns once some backend waits on a lock in a statement naming `table`."""
    async with asyncio.timeout(WAIT_SECONDS), engine.connect() as connection:
        while True:
            waiting = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' AND query ILIKE :statement"
                ),
                {"statement": f"%{table}%"},
            )
            await connection.rollback()
            if waiting:
                return
            await asyncio.sleep(0.01)
