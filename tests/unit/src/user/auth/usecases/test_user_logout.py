from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.auth.credentials import SessionIdentity
from src.core.schemas import SuccessResponse
from src.user.auth.usecases.logout import UserLogoutUseCase
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork


def build_use_case(
    session: FakeAsyncSession, logout: AsyncMock
) -> tuple[UserLogoutUseCase, FakeUnitOfWork]:
    uow = FakeUnitOfWork(session=session)
    return UserLogoutUseCase(uow=uow, logout=logout), uow


@pytest.mark.asyncio
async def test_logout_records_which_sessions_it_ended(
    fake_session: FakeAsyncSession,
) -> None:
    """A sign-out of every session is what an account takeover looks like from
    the outside, so the row has to say which of the two happened."""
    subject_id = uuid4()
    logout = AsyncMock()
    logout.execute = AsyncMock(return_value=SuccessResponse(success=True))
    use_case, uow = build_use_case(fake_session, logout)

    await use_case.execute(
        identity=SessionIdentity(subject_id=str(subject_id), session_id="session-1"),
        terminate_all_sessions=True,
        ip="10.0.0.1",
    )

    actor, event = uow.event_logs.recorded[0]
    assert actor.actor_id == subject_id
    assert actor.ip == "10.0.0.1"
    assert event.code == "user.signed_out"
    assert event.all_sessions is True
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sessions_are_invalidated_before_the_row_is_written(
    fake_session: FakeAsyncSession,
) -> None:
    """Ending the session is what the caller is owed; the audit row is not worth
    delaying it, and a failure to end it must leave no row claiming otherwise."""
    order: list[str] = []
    logout = AsyncMock()
    logout.execute = AsyncMock(
        side_effect=lambda **_: order.append("invalidate")
        or SuccessResponse(success=True)
    )
    use_case, uow = build_use_case(fake_session, logout)
    uow.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    await use_case.execute(
        identity=SessionIdentity(subject_id=str(uuid4()), session_id="session-1")
    )

    assert order == ["invalidate", "commit"]
