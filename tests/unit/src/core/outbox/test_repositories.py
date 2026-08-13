from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.core.outbox.repositories import OutboxRepository
from tests.fakes.db import FakeAsyncSession


def _compiled_sql(fake_session: FakeAsyncSession) -> str:
    query = fake_session.execute.await_args.args[0]
    return str(query.compile(dialect=postgresql.dialect()))


async def test_get_batch_for_publish_locks_pending_fifo() -> None:
    session = FakeAsyncSession()
    session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )

    await OutboxRepository().get_batch_for_publish(session, limit=100)

    sql = _compiled_sql(session)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY outbox_messages.created_at" in sql
    assert "LIMIT" in sql


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


async def test_purge_published_filters_status_and_cutoff() -> None:
    from datetime import timedelta

    from src.core.utils.datetime_utils import get_utc_now

    session = FakeAsyncSession()
    session.execute.return_value = MagicMock(rowcount=3)

    deleted = await OutboxRepository().purge_published(
        session, cutoff=get_utc_now() - timedelta(days=7)
    )

    assert deleted == 3
    sql = _compiled_sql(session)
    assert "DELETE FROM outbox_messages" in sql
    assert "status" in sql and "published_at" in sql
