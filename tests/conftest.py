from collections.abc import AsyncGenerator, Generator
import os
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx2
import pytest
import pytest_asyncio

from src.core.cache.dependencies import get_cache
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.core.database.session import get_session, get_unit_of_work
from src.core.email_service.dependencies import get_email_service
from src.core.email_service.service import EmailService
from src.core.redis.dependencies import get_redis_client
from src.core.storage.s3.dependencies import get_s3_adapter
from src.main.config import Config, get_settings
from src.main.web import get_application
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.fakes.s3 import InMemoryS3Client
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


@pytest.fixture(scope="session")
def settings() -> Config:
    os.environ.setdefault("TESTING", "true")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def app() -> FastAPI:
    return get_application()


@pytest.fixture
def dependency_overrides(app: FastAPI) -> Generator[DependencyOverrides]:
    overrides = DependencyOverrides(app)
    yield overrides
    overrides.reset()


@pytest.fixture(autouse=True)
def cache() -> Generator[InMemoryCache]:
    # Autouse for every test, not only app_with_fakes: @cached_route resolves the
    # cache via get_cache_instance() at call time, so any test hitting a decorated
    # route through the plain async_client fixture needs a bound instance too.
    instance = InMemoryCache(
        serializer=JsonSerializer(),
        default_ttl=60,
        version_ttl=604800,
    )
    set_cache(instance)
    yield instance
    reset_cache()


@pytest.fixture
def fake_redis() -> InMemoryRedis:
    return InMemoryRedis()


@pytest.fixture
def fake_s3() -> InMemoryS3Client:
    return InMemoryS3Client()


@pytest.fixture
def mock_mailer() -> MockMailer:
    return MockMailer()


@pytest.fixture
def email_dispatcher() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def email_service(mock_mailer: MockMailer, email_dispatcher: AsyncMock) -> EmailService:
    return EmailService(mock_mailer, email_dispatcher)


@pytest.fixture
def fake_session() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def fake_uow(fake_session: FakeAsyncSession) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=fake_session)


@pytest.fixture
def app_with_fakes(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    fake_redis: InMemoryRedis,
    fake_s3: InMemoryS3Client,
    email_service: EmailService,
    fake_session: FakeAsyncSession,
    fake_uow: FakeUnitOfWork,
    settings: Config,
    cache: InMemoryCache,
) -> FastAPI:
    dependency_overrides.set(get_redis_client, ProvideValue(fake_redis))
    dependency_overrides.set(get_s3_adapter, ProvideAsyncValue(fake_s3))
    dependency_overrides.set(get_email_service, ProvideValue(email_service))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(get_unit_of_work, ProvideAsyncValue(fake_uow))
    dependency_overrides.set(get_settings, ProvideValue(settings))
    dependency_overrides.set(get_cache, ProvideAsyncValue(cache))
    return app


@pytest_asyncio.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def async_client_with_fakes(
    app_with_fakes: FastAPI,
) -> AsyncGenerator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app_with_fakes)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client
