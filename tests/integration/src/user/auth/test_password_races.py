"""Sign-in against a concurrent password change or reset.

Each race is forced: one side is paused at a known point while the other runs to
completion, or to a lock wait PostgreSQL itself reports.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from argon2 import PasswordHasher
import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.auth.errors import InvalidCredentialsError
from src.core.cache.memory_cache import InMemoryCache
from src.core.database.uow import ApplicationUnitOfWork
from src.core.utils.security import hash_password, verify_password
from src.event_log.actor import Actor
from src.event_log.models import EventLog
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import LoginUserModel, ResetPasswordModel, UserNewPassword
from src.user.auth.usecases import login, reset_password_confirm
from src.user.models import User
from src.user.usecases import update_password
from tests.fakes.redis import InMemoryRedis

pytestmark = pytest.mark.asyncio(loop_scope="session")

OLD = "CurrentPass1!"
NEW = "StrongPass1!"
RESET = "ResetPass1!"
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


class Actors:
    """The three flows over one account, each call on a session of its own."""

    def __init__(self, engine: AsyncEngine, user: User, cache: InMemoryCache) -> None:
        self.engine = engine
        self.user = user
        self.redis = InMemoryRedis()
        self.cache = cache
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def login(self, password: str = OLD) -> Any:
        async with self.sessions() as session:
            return await login.LoginUserUseCase(
                ApplicationUnitOfWork(session), self.redis, self.cache
            ).execute(LoginUserModel(email=self.user.email, password=password))

    async def change(self) -> Any:
        async with self.sessions() as session:
            return await update_password.UpdateUserPasswordUseCase(
                ApplicationUnitOfWork(session), self.redis, self.cache
            ).execute(
                UserNewPassword(current_password=OLD, password=NEW),
                self.user.id,
                Actor.user(self.user.id),
            )

    async def reset(self) -> Any:
        async with self.sessions() as session:
            return await reset_password_confirm.ResetPasswordConfirmUseCase(
                ApplicationUnitOfWork(session), self.redis, self.cache
            ).execute(ResetPasswordModel(token="token", password=RESET))

    async def stored_hash(self) -> str:
        async with self.sessions() as session:
            user = await session.get_one(User, self.user.id)
            return user.password_hash

    async def live_sessions(self) -> list[str]:
        key = USER_AUTH_REALM.keys.sessions(str(self.user.id))
        return list(await self.redis.zrange(key, 0, -1))

    async def wait_for_lock_wait(self) -> None:
        """Returns once some backend waits on a row lock over `users`."""
        async with asyncio.timeout(WAIT_SECONDS), self.engine.connect() as connection:
            while True:
                waiting = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock' AND query ILIKE '%users%'"
                    )
                )
                await connection.rollback()
                if waiting:
                    return
                await asyncio.sleep(0.01)


@pytest_asyncio.fixture(loop_scope="session")
async def actors(
    integration_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> AsyncGenerator[Actors]:
    marker = uuid4().hex[:8]
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        user = User(
            first_name="Race",
            last_name="User",
            email=f"race-{marker}@example.com",
            username=f"race{marker}",
            phone_number="+12025550179",
            password_hash=await hash_password(OLD),
            is_verified=True,
        )
        session.add(user)
        await session.commit()
    monkeypatch.setattr(
        reset_password_confirm,
        "decode_one_time_token",
        AsyncMock(return_value=user.email),
    )
    yield Actors(integration_engine, user, cache)
    async with AsyncSession(integration_engine) as session:
        await session.execute(
            delete(EventLog).where(
                (EventLog.actor_id == user.id)
                | (EventLog.payload["email"].astext == user.email)
            )
        )
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


def pause(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, *, after: bool
) -> Pause:
    paused = Pause(getattr(module, name), after=after)
    monkeypatch.setattr(module, name, paused)
    return paused


async def test_sign_in_verified_before_a_change_gets_no_session(
    actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused = pause(monkeypatch, login, "verify_password", after=True)
    task = asyncio.create_task(actors.login())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)

    await actors.change()
    paused.release.set()

    with pytest.raises(InvalidCredentialsError):
        await task
    assert await actors.live_sessions() == []


async def test_a_pending_rehash_never_restores_the_old_password(
    actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with actors.sessions() as session:
        user = await session.get_one(User, actors.user.id)
        user.password_hash = PasswordHasher(time_cost=1, memory_cost=8192).hash(OLD)
        await session.commit()
    paused = pause(monkeypatch, login, "hash_password", after=True)
    task = asyncio.create_task(actors.login())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)

    await actors.change()
    paused.release.set()

    with pytest.raises(InvalidCredentialsError):
        await task
    assert await verify_password(NEW, await actors.stored_hash())


@pytest.mark.parametrize("flow", ["change", "reset"])
async def test_a_session_issued_under_the_lock_is_wiped(
    actors: Actors, monkeypatch: pytest.MonkeyPatch, flow: str
) -> None:
    """The change must wait for the sign-in, or its wipe runs before the session
    exists and the session survives."""
    paused = pause(monkeypatch, login, "issue_session_pair", after=False)
    sign_in = asyncio.create_task(actors.login())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)

    writer = asyncio.create_task(getattr(actors, flow)())
    await actors.wait_for_lock_wait()
    paused.release.set()

    assert (await sign_in).access_token
    assert (await writer).success
    assert await actors.live_sessions() == []


async def test_a_change_never_overwrites_a_reset_committed_meanwhile(
    actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused = pause(monkeypatch, update_password, "hash_password", after=True)
    change = asyncio.create_task(actors.change())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)

    await actors.reset()
    paused.release.set()

    with pytest.raises(InvalidCredentialsError):
        await change
    assert await verify_password(RESET, await actors.stored_hash())


async def test_a_change_waiting_on_a_reset_decides_on_the_new_row(
    actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both reach the row lock: without the locked read the change compares the
    hash from before the reset and overwrites it."""
    paused = pause(
        monkeypatch, reset_password_confirm, "invalidate_all_sessions", after=True
    )
    reset = asyncio.create_task(actors.reset())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)

    change = asyncio.create_task(actors.change())
    await actors.wait_for_lock_wait()
    paused.release.set()

    assert (await reset).success
    with pytest.raises(InvalidCredentialsError):
        await change
    assert await verify_password(RESET, await actors.stored_hash())


@pytest.mark.parametrize(
    ("flow", "module"), [("login", login), ("change", update_password)]
)
async def test_no_transaction_is_open_while_the_password_is_verified(
    actors: Actors, monkeypatch: pytest.MonkeyPatch, flow: str, module: Any
) -> None:
    """The fake session does not autobegin on a read, so only a real one shows
    a lookup that left its transaction open."""
    paused = pause(monkeypatch, module, "verify_password", after=False)
    idle_before = actors.engine.pool.checkedout()  # type: ignore[attr-defined]
    task = asyncio.create_task(getattr(actors, flow)())
    await asyncio.wait_for(paused.reached.wait(), WAIT_SECONDS)
    held = actors.engine.pool.checkedout() - idle_before  # type: ignore[attr-defined]
    paused.release.set()
    await task

    assert held == 0
