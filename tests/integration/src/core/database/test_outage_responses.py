"""What a request answers when PostgreSQL goes away, and when it merely refuses.

Which exception a lost or refused connection surfaces as is decided by asyncpg and
SQLAlchemy together, so only the real pair shows it: none of these is an
OperationalError until the engine's `do_connect` listener makes one.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import contextlib
import logging
import socket
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
import httpx2
import pytest
import pytest_asyncio
from sqlalchemy import event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.core.auth.dependencies import load_principal
from src.core.database.engine import connect_or_operational_error
from src.core.database.uow import ApplicationUnitOfWork
import src.core.middleware as middleware
from src.event_log.actor import Actor
from src.main.config import get_settings
from src.user.events import UserSignedIn
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

WAIT_SECONDS = 10
Handler = Callable[[], Awaitable[Any]]


def build_engine(url: Any = None, **options: Any) -> AsyncEngine:
    connect_args = {"statement_cache_size": 0, **options.pop("connect_args", {})}
    engine = create_async_engine(
        url or get_settings().postgres.dsn_async,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args=connect_args,
        **options,
    )
    event.listen(engine.sync_engine, "do_connect", connect_or_operational_error)
    return engine


@pytest_asyncio.fixture(loop_scope="session")
async def engine(database_template: str) -> AsyncGenerator[AsyncEngine]:
    engine = build_engine()
    yield engine
    await engine.dispose()


@pytest.fixture
def reported(monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    captured: list[BaseException] = []
    monkeypatch.setattr(middleware.sentry_sdk, "capture_exception", captured.append)
    return captured


async def answer(handler: Handler) -> httpx2.Response:
    app = FastAPI()
    middleware.register_middlewares(app)

    @app.get("/")
    async def endpoint() -> None:
        await handler()

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
        return await asyncio.wait_for(http.get("/"), WAIT_SECONDS)


def select_one(engine: AsyncEngine) -> Handler:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            await session.execute(text("SELECT 1"))

    return handler


async def backend_pid(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())


async def terminate(pid: int) -> None:
    """Kill the backend and wait until the server has let go of it."""
    killer = create_async_engine(get_settings().postgres.dsn_async)
    try:
        async with killer.connect() as connection:
            await connection.execute(
                text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": pid}
            )
    finally:
        await killer.dispose()


def assert_outage(response: httpx2.Response, reported: list[BaseException]) -> None:
    assert (response.status_code, response.json()["code"]) == (
        503,
        "service_unavailable",
    )
    assert len(reported) == 1


async def test_a_refused_connection_is_an_outage(
    reported: list[BaseException],
) -> None:
    # The local port of an open connection: taken, so nothing else listens on it,
    # and refused at once, where macOS lets a merely bound one time out.
    with (
        socket.create_server(("127.0.0.1", 0)) as server,
        socket.create_connection(server.getsockname()) as held,
    ):
        engine = build_engine(
            make_url(get_settings().postgres.dsn_async).set(
                host="127.0.0.1", port=held.getsockname()[1]
            )
        )
        response = await answer(select_one(engine))

    assert_outage(response, reported)
    assert str(reported[0]) == "OperationalError driver=ConnectionRefusedError"


@pytest.fixture
def no_name_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_: Any, **__: Any) -> None:
        raise socket.gaierror(socket.EAI_NONAME, "unknown host")

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fail)


async def test_an_unresolved_host_is_an_outage(
    no_name_resolves: None, reported: list[BaseException]
) -> None:
    engine = build_engine(
        make_url(get_settings().postgres.dsn_async).set(host="database.invalid")
    )

    assert_outage(await answer(select_one(engine)), reported)
    assert "driver=gaierror" in str(reported[0])


async def test_a_server_that_never_answers_the_handshake_is_an_outage(
    reported: list[BaseException],
) -> None:
    async def hold(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(WAIT_SECONDS)
        writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    engine = build_engine(
        make_url(get_settings().postgres.dsn_async).set(
            host="127.0.0.1", port=server.sockets[0].getsockname()[1]
        ),
        connect_args={"timeout": 0.2},
    )
    try:
        assert_outage(await answer(select_one(engine)), reported)
    finally:
        server.close()
    assert "driver=TimeoutError" in str(reported[0])


async def test_an_exhausted_pool_is_an_outage(
    database_template: str, reported: list[BaseException]
) -> None:
    engine = build_engine(pool_size=1, max_overflow=0, pool_timeout=0.1)
    try:
        async with engine.connect():
            response = await answer(select_one(engine))
    finally:
        await engine.dispose()

    assert_outage(response, reported)


async def test_a_connection_killed_mid_query_is_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            pid = await backend_pid(session)
            sleeping = asyncio.create_task(session.execute(text("SELECT pg_sleep(30)")))
            await asyncio.sleep(0)
            await terminate(pid)
            await sleeping

    assert_outage(await answer(handler), reported)


async def test_a_connection_reset_mid_query_is_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            pid = await backend_pid(session)
            connection = await session.connection()
            raw = (await connection.get_raw_connection()).driver_connection
            sleeping = asyncio.create_task(session.execute(text("SELECT pg_sleep(30)")))
            await asyncio.sleep(0.05)
            # What a TCP reset looks like to the protocol, without a proxy.
            raw._protocol.connection_lost(ConnectionResetError())
            # The server sleeps on, and nobody may copy a database in use.
            await terminate(pid)
            await sleeping

    assert_outage(await answer(handler), reported)


async def test_a_connection_killed_before_the_commit_is_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            async with ApplicationUnitOfWork(session) as uow:
                await terminate(await backend_pid(uow.session))
                await uow.commit()

    assert_outage(await answer(handler), reported)


async def test_a_rollback_on_a_killed_connection_does_not_hide_the_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    """The unit of work rolls back on the way out of the failure."""

    async def handler() -> None:
        async with AsyncSession(engine) as session:
            async with ApplicationUnitOfWork(session) as uow:
                await terminate(await backend_pid(uow.session))
                await uow.session.execute(text("SELECT 1"))

    assert_outage(await answer(handler), reported)


async def test_a_bare_rollback_on_a_killed_connection_is_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            await terminate(await backend_pid(session))
            await session.rollback()

    assert_outage(await answer(handler), reported)


async def test_a_journal_write_on_a_killed_connection_is_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            async with ApplicationUnitOfWork(session) as uow:
                await terminate(await backend_pid(uow.session))
                await uow.publish(Actor.system(), UserSignedIn(object_id=uuid4()))
                await uow.commit()

    assert_outage(await answer(handler), reported)


async def test_an_unreachable_database_in_the_auth_dependency_is_an_outage(
    no_name_resolves: None,
    reported: list[BaseException],
) -> None:
    engine = build_engine(
        make_url(get_settings().postgres.dsn_async).set(host="database.invalid")
    )

    async def handler() -> None:
        async with AsyncSession(engine) as session:
            await load_principal(session, UserRepository(), str(uuid4()))

    assert_outage(await answer(handler), reported)


async def test_a_connection_killed_in_the_auth_dependency_is_an_outage(
    engine: AsyncEngine,
    reported: list[BaseException],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            repository = UserRepository()
            read = repository.get_single

            async def read_after_kill(*args: Any, **filters: Any) -> Any:
                await terminate(await backend_pid(session))
                return await read(*args, **filters)

            monkeypatch.setattr(repository, "get_single", read_after_kill)
            await load_principal(session, repository, str(uuid4()))

    assert_outage(await answer(handler), reported)


@contextlib.asynccontextmanager
async def login_role(options: str) -> AsyncGenerator[str]:
    name = f"outage_{uuid4().hex[:12]}"
    admin = create_async_engine(
        get_settings().postgres.dsn_async, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(
                text(f"CREATE ROLE {name} LOGIN PASSWORD 'role-pw' {options}")
            )
            try:
                yield name
            finally:
                await connection.execute(text(f"DROP ROLE {name}"))
    finally:
        await admin.dispose()


async def test_no_connection_slot_is_an_outage(
    database_template: str, reported: list[BaseException]
) -> None:
    async with login_role("CONNECTION LIMIT 0") as role:
        engine = build_engine(
            make_url(get_settings().postgres.dsn_async).set(
                username=role, password="role-pw"
            )
        )
        response = await answer(select_one(engine))

    assert_outage(response, reported)
    assert "sqlstate=53300" in str(reported[0])


@pytest.mark.parametrize(
    ("url", "sqlstate"),
    [({"password": "wrong"}, "28P01"), ({"database": "missing"}, "3D000")],
    ids=["password", "database"],
)
async def test_a_misconfigured_connection_is_not_an_outage(
    database_template: str,
    reported: list[BaseException],
    url: dict[str, str],
    sqlstate: str,
) -> None:
    """A 503 says "try later", and no later try succeeds."""
    engine = build_engine(make_url(get_settings().postgres.dsn_async).set(**url))

    response = await answer(select_one(engine))

    assert response.status_code == 500
    assert f"sqlstate={sqlstate}" in str(reported[0])


async def test_a_statement_timeout_is_not_an_outage(
    engine: AsyncEngine, reported: list[BaseException]
) -> None:
    async def handler() -> None:
        async with AsyncSession(engine) as session:
            await session.execute(text("SET LOCAL statement_timeout = 10"))
            await session.execute(text("SELECT pg_sleep(5)"))

    response = await answer(handler)

    assert response.status_code == 500
    assert "sqlstate=57014" in str(reported[0])


async def test_a_bound_value_the_driver_refuses_stays_out_of_the_report(
    engine: AsyncEngine,
    reported: list[BaseException],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """asyncpg quotes the value in its own text, which hide_parameters does not
    touch, and the class it raises derives from its InterfaceError."""
    value = "secret@example.com"

    async def handler() -> None:
        async with AsyncSession(engine) as session:
            await session.execute(text("SELECT CAST(:n AS integer)"), {"n": value})

    middleware.logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        response = await answer(handler)
    finally:
        middleware.logger.removeHandler(caplog.handler)

    assert response.status_code == 500
    assert value not in caplog.text
    assert "driver=DataError" in caplog.text
    assert value not in str(reported[0])
    assert reported[0].__cause__ is None
