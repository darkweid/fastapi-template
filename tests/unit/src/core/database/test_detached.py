import pytest

from src.core.database.detached import detached_read
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession


async def test_ends_the_read_transaction_it_opened() -> None:
    """Left open, it pins a pooled connection idle in transaction for the
    rest of the request, however long the handler then waits."""
    session = FakeAsyncSession()

    async with detached_read(session) as read:
        read.detach(build_user(), None)

    session.rollback.assert_awaited_once()


async def test_ends_it_when_the_block_raises() -> None:
    """A 404 raised after the read must not leave the connection held until
    the error response is written."""
    session = FakeAsyncSession()

    with pytest.raises(LookupError):
        async with detached_read(session):
            raise LookupError

    session.rollback.assert_awaited_once()


async def test_leaves_a_transaction_it_did_not_open() -> None:
    """Rolling back someone else's transaction would discard their work."""
    session = FakeAsyncSession(in_transaction=True)

    async with detached_read(session) as read:
        read.detach(build_user())

    session.rollback.assert_not_awaited()


async def test_an_instance_never_attached_is_not_expunged() -> None:
    """Expunging an instance the session does not hold raises; a factory-built
    or already detached one passes through untouched."""
    session = FakeAsyncSession()

    async with detached_read(session) as read:
        read.detach(build_user())

    session.expunge.assert_not_called()
