"""A mailed link spent while the account behind it is blocked or deleted.

The other session holds its change uncommitted until PostgreSQL reports the
link's flow waiting on the row; the flow's own session has loaded the old row.
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import UpdateBase

from src.core.auth.errors import RefreshTokenReuseError
from src.core.cache.memory_cache import InMemoryCache
from src.core.database.uow import ApplicationUnitOfWork
from src.event_log.models import EventLog
from src.user.auth import refresh_reuse
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import ResetPasswordModel
from src.user.auth.usecases import reset_password_confirm, verify_email
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.redis import InMemoryRedis
from tests.helpers.races import WAIT_SECONDS, wait_for_lock_wait

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Link:
    def __init__(
        self, engine: AsyncEngine, user_id: UUID, email: str, cache: InMemoryCache
    ) -> None:
        self.engine = engine
        self.user_id = user_id
        self.email = email
        self.cache = cache
        self.redis = InMemoryRedis()
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _spend(self, session: AsyncSession, flow: str) -> Any:
        uow = ApplicationUnitOfWork(session)
        if flow == "verify":
            return await verify_email.VerifyEmailUseCase(
                uow, self.redis, self.cache
            ).execute("token")
        return await reset_password_confirm.ResetPasswordConfirmUseCase(
            uow, self.redis, self.cache
        ).execute(ResetPasswordModel(token="token", password="StrongPass1!"))

    async def spend_against(self, flow: str, change: UpdateBase) -> Any:
        async with self.sessions() as holder, self.sessions() as session:
            # Held: the identity map is weak, and a collected row is not stale.
            stale = await session.get(User, self.user_id)
            assert stale is not None
            await session.commit()
            await holder.execute(change)
            spending = asyncio.ensure_future(self._spend(session, flow))
            try:
                await wait_for_lock_wait(self.engine, "users")
            finally:
                await holder.commit()
                await asyncio.wait([spending], timeout=WAIT_SECONDS)
            return await asyncio.wait_for(spending, WAIT_SECONDS)

    async def events(self) -> list[EventLog]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(EventLog).where(EventLog.object_id == self.user_id)
            )
            return list(rows)


@pytest_asyncio.fixture(loop_scope="session")
async def link(
    integration_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> AsyncGenerator[Link]:
    user = build_user(
        email=f"{uuid4().hex}@example.com", username=uuid4().hex, is_verified=False
    )
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        session.add(user)
        await session.commit()
    for module in (verify_email, reset_password_confirm):
        monkeypatch.setattr(
            module, "decode_one_time_token", AsyncMock(return_value=user.email)
        )
    yield Link(integration_engine, user.id, user.email, cache)
    async with AsyncSession(integration_engine) as session:
        await session.execute(delete(EventLog).where(EventLog.object_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.mark.parametrize("flow", ["verify", "reset"])
@pytest.mark.parametrize("change", ["block", "delete"])
async def test_a_link_is_refused_once_its_account_is_blocked_or_gone(
    link: Link, flow: str, change: str
) -> None:
    """A check on the row read before the wait would verify, or hand a new
    password to, an account an admin has just shut."""
    statement: UpdateBase = delete(User).where(User.id == link.user_id)
    if change == "block":
        statement = update(User).where(User.id == link.user_id).values(is_active=False)
    failures = USER_AUTH_REALM.keys.login_failures(link.email)
    sessions = USER_AUTH_REALM.keys.sessions(str(link.user_id))
    await link.redis.set(failures, "5", ex=600)
    await link.redis.zadd(sessions, {"session": 1})

    result = await link.spend_against(flow, statement)

    assert result.success is False
    assert await link.events() == []
    assert await link.redis.get(failures) == "5"
    assert await link.redis.zrange(sessions, 0, -1) == ["session"]


async def test_a_recorded_reuse_is_committed_on_a_session_of_its_own(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request that detected it answers 401 and commits nothing itself."""
    monkeypatch.setattr(refresh_reuse, "async_session", link.sessions)

    await refresh_reuse.record_refresh_reuse(
        RefreshTokenReuseError(
            "reuse", subject_id=str(link.user_id), session_id="session"
        ),
        "203.0.113.7",
    )

    [event] = await link.events()
    assert event.event_type == "user.refresh_reuse_detected"
    assert event.payload == {"session_id": "session"}
    assert event.actor_id is None and str(event.ip_address) == "203.0.113.7"
