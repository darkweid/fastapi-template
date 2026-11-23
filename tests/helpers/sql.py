from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


@contextmanager
def count_statements(engine: AsyncEngine) -> Generator[list[str]]:
    """The statements sent through `engine` while the block runs."""
    statements: list[str] = []

    def record(_connection: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
