"""What an authenticated request holds on to after the auth dependency ran.

A handler that waits on something slow outside any unit of work (an upload, a
probe, a third-party call) must not keep a pooled connection checked out and
idle in transaction for that whole wait: a few such requests at once drain a
worker's pool, and the open transaction holds back vacuum. Only a real engine
and `pg_stat_activity` can show what the connection is doing.
"""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
import httpx2
import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.database.session import get_session, get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.redis.dependencies import get_redis_client
from src.core.utils.security import password_hasher
from src.main.config import get_settings
from src.user.auth.dependencies import get_current_user
from src.user.enums import UserRole
from src.user.models import User
from src.user.repositories import UserRepository
from tests.factories.token_factory import build_access_token
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue

pytestmark = pytest.mark.asyncio(loop_scope="session")

WAIT_SECONDS = 5


@dataclass
class SlowWork:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


router = APIRouter()


@router.get("/slow")
async def wait_outside_any_unit_of_work(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    work: SlowWork = request.app.state.slow_work
    work.entered.set()
    await work.release.wait()
    return {"email": user.email}


@router.get("/after-rollback")
async def read_principal_after_a_rolled_back_unit_of_work(
    user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
) -> dict[str, str]:
    async with uow:
        await uow.users.get_single(uow.session, id=user.id)
    return {"email": user.email}


class RequestSessions:
    """`get_session` on an engine of the test's own, so its pool counts only
    what the request under test checks out."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def __call__(self) -> AsyncGenerator[AsyncSession]:
        async with self._factory() as session:
            yield session


@pytest.fixture
def application_name() -> str:
    return f"auth-connection-{uuid4().hex[:12]}"


@pytest_asyncio.fixture(loop_scope="session")
async def request_engine(application_name: str) -> AsyncGenerator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().postgres.dsn_async,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {"application_name": application_name},
        },
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def stored_user(integration_engine: AsyncEngine) -> AsyncGenerator[User]:
    tag = f"auth-conn-{uuid4().hex[:12]}"
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        user = await UserRepository().create(
            session,
            {
                "first_name": "Idle",
                "last_name": "Connection",
                "email": f"{tag}@example.com",
                "username": tag,
                "phone_number": "+10000000000",
                "password_hash": password_hasher.hash("integration-password"),
                "role": UserRole.VIEWER,
                "is_verified": True,
                "is_active": True,
            },
            commit=True,
        )
    yield user
    async with AsyncSession(integration_engine) as session:
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def client(
    request_engine: AsyncEngine,
) -> AsyncGenerator[tuple[httpx2.AsyncClient, InMemoryRedis, SlowWork]]:
    redis = InMemoryRedis()
    work = SlowWork()
    app = FastAPI()
    app.include_router(router)
    app.state.slow_work = work
    app.dependency_overrides[get_session] = RequestSessions(request_engine)
    app.dependency_overrides[get_redis_client] = ProvideValue(redis)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http_client:
        yield http_client, redis, work


async def _connection_states(engine: AsyncEngine, application_name: str) -> list[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT state FROM pg_stat_activity "
                "WHERE application_name = :name AND datname = current_database()"
            ),
            {"name": application_name},
        )
        return [row.state for row in rows]


async def test_slow_handler_after_authentication_holds_no_connection(
    client: tuple[httpx2.AsyncClient, InMemoryRedis, SlowWork],
    stored_user: User,
    request_engine: AsyncEngine,
    integration_engine: AsyncEngine,
    application_name: str,
) -> None:
    """The auth dependency's SELECT used to autobegin a transaction on the
    request session that lived until the response: the whole wait below ran
    with a checked-out connection sitting idle in transaction."""
    http_client, redis, work = client
    token = await build_access_token({"sub": str(stored_user.id)}, redis)

    request = asyncio.create_task(
        http_client.get("/slow", headers={"Authorization": token})
    )
    entered = asyncio.create_task(work.entered.wait())
    try:
        # A request that fails before reaching the handler finishes first, and
        # its response below says why instead of a bare timeout.
        await asyncio.wait(
            {entered, request},
            timeout=WAIT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert work.entered.is_set(), (await request).text

        states = await _connection_states(integration_engine, application_name)
        checked_out = request_engine.pool.checkedout()  # type: ignore[attr-defined]
    finally:
        entered.cancel()
        work.release.set()
        response = await asyncio.wait_for(request, WAIT_SECONDS)

    assert "idle in transaction" not in states
    assert checked_out == 0
    assert response.status_code == 200
    assert response.json() == {"email": stored_user.email}


async def test_principal_stays_readable_after_a_unit_of_work_rolls_back(
    client: tuple[httpx2.AsyncClient, InMemoryRedis, SlowWork],
    stored_user: User,
) -> None:
    """A unit of work in an authenticated request is a top-level transaction,
    and rolling one back expires every instance its session holds. The principal
    must not be one of them, or the next attribute read lazy-loads outside any
    greenlet."""
    http_client, redis, _ = client
    token = await build_access_token({"sub": str(stored_user.id)}, redis)

    response = await http_client.get(
        "/after-rollback", headers={"Authorization": token}
    )

    assert response.status_code == 200
    assert response.json() == {"email": stored_user.email}
