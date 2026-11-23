from unittest.mock import AsyncMock

from aiosmtplib import SMTPException
import pytest

from src.core.database.uow.sqlalchemy import SQLAlchemyUnitOfWork, logger
from tests.fakes.db import FakeAsyncSession
from tests.helpers.logs import capture_logger


@pytest.fixture
def uow() -> SQLAlchemyUnitOfWork:
    return SQLAlchemyUnitOfWork(FakeAsyncSession())


async def test_hooks_run_in_order_after_commit(uow: SQLAlchemyUnitOfWork) -> None:
    order: list[str] = []

    async def first() -> None:
        order.append("first")

    async def second() -> None:
        order.append("second")

    async with uow:
        uow.add_after_commit_hook(first)
        uow.add_after_commit_hook(second)
        assert order == []  # nothing fires before commit
        await uow.commit()

    assert order == ["first", "second"]


async def test_hooks_do_not_run_on_rollback(uow: SQLAlchemyUnitOfWork) -> None:
    hook = AsyncMock()
    async with uow:
        uow.add_after_commit_hook(hook)
        await uow.rollback()
    hook.assert_not_awaited()


async def test_hooks_do_not_run_on_exception_rollback(
    uow: SQLAlchemyUnitOfWork,
) -> None:
    hook = AsyncMock()
    with pytest.raises(RuntimeError, match="boom"):
        async with uow:
            uow.add_after_commit_hook(hook)
            raise RuntimeError("boom")
    hook.assert_not_awaited()


async def test_failing_hook_is_logged_and_others_still_run(
    uow: SQLAlchemyUnitOfWork,
) -> None:
    ran: list[str] = []

    async def broken() -> None:
        raise RuntimeError("hook failed")

    async def healthy() -> None:
        ran.append("healthy")

    async with uow:
        uow.add_after_commit_hook(broken)
        uow.add_after_commit_hook(healthy)
        await uow.commit()  # must not raise

    assert ran == ["healthy"]


async def test_add_hook_after_completion_raises(uow: SQLAlchemyUnitOfWork) -> None:
    async with uow:
        await uow.commit()
    with pytest.raises(RuntimeError):
        uow.add_after_commit_hook(AsyncMock())


async def test_a_failing_hook_logs_no_address(
    uow: SQLAlchemyUnitOfWork, caplog: pytest.LogCaptureFixture
) -> None:
    """A hook publishes tasks and invalidates caches: an SMTP or Redis error it
    raises can quote what it was handed."""

    refused = SMTPException("refused: secret@example.com")

    async def broken() -> None:
        raise refused

    with capture_logger(caplog, logger):
        async with uow:
            uow.add_after_commit_hook(broken)
            await uow.commit()

    assert "After-commit hook failed" in caplog.text
    assert "SMTPException" in caplog.text
    assert "secret@example.com" not in caplog.text
