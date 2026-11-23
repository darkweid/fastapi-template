from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.core.outbox.enums import OutboxMessageStatus
from src.core.outbox.repositories import OutboxRepository
from src.core.utils.datetime_utils import get_utc_now
from tests.fakes.db import FakeAsyncSession


def _compiled_sql(fake_session: FakeAsyncSession) -> str:
    query = fake_session.execute.await_args.args[0]
    return str(query.compile(dialect=postgresql.dialect()))


async def test_get_batch_for_publish_locks_pending_fifo() -> None:
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )

    await OutboxRepository().get_batch_for_publish(
        session, limit=100, min_age=timedelta(seconds=60)
    )

    sql = _compiled_sql(session)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY outbox_messages.created_at" in sql
    assert "LIMIT" in sql
    # The database clock, not a timestamp computed on the app server.
    assert "outbox_messages.created_at < now() -" in sql


async def test_mark_publish_failure_final_flips_status() -> None:
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock()

    await OutboxRepository().mark_publish_failure(
        session, uuid4(), error="boom", final=True
    )

    query = session.execute.await_args.args[0]
    values = query._values  # noqa: SLF001 - inspecting the emitted UPDATE
    assert "status" in {c.key for c in values}


async def test_mark_publish_failure_not_final_keeps_status() -> None:
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock()

    await OutboxRepository().mark_publish_failure(
        session, uuid4(), error="boom", final=False
    )

    query = session.execute.await_args.args[0]
    assert "status" not in {c.key for c in query._values}  # noqa: SLF001


async def test_purge_deletes_a_bounded_batch() -> None:
    """Inlined, the planner may run the LIMIT subquery more than once per
    statement and delete more than the limit."""
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock(rowcount=3)

    deleted = await OutboxRepository().purge(
        session, OutboxMessageStatus.PUBLISHED, get_utc_now(), limit=10
    )

    assert deleted == 3
    sql = _compiled_sql(session)
    assert "AS MATERIALIZED" in sql
    assert "LIMIT" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


async def test_purge_refuses_a_status_that_is_still_to_deliver() -> None:
    with pytest.raises(KeyError):
        await OutboxRepository().purge(
            FakeAsyncSession(),
            OutboxMessageStatus.PENDING,  # type: ignore[arg-type]
            get_utc_now(),
            limit=10,
        )


async def test_mark_publish_failure_can_leave_attempts_alone() -> None:
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock()

    await OutboxRepository().mark_publish_failure(
        session, uuid4(), error="broker down", final=False, count_attempt=False
    )

    query = session.execute.await_args.args[0]
    assert {c.key for c in query._values} == {"last_error"}  # noqa: SLF001
