from collections.abc import AsyncGenerator, Generator
import os

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from src.core.database.session import get_session, get_unit_of_work
from src.core.email_service.dependencies import get_email_service
from src.core.email_service.service import EmailService
from src.core.redis.dependencies import get_redis_client
from src.core.storage.s3.dependencies import get_s3_adapter
from src.main.config import Config, get_settings
from src.main.web import get_application
from tests.email.mocks import MockMailer
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
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
def email_service(mock_mailer: MockMailer) -> EmailService:
    return EmailService(mock_mailer)


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
) -> FastAPI:
    dependency_overrides.set(get_redis_client, ProvideValue(fake_redis))
    dependency_overrides.set(get_s3_adapter, ProvideAsyncValue(fake_s3))
    dependency_overrides.set(get_email_service, ProvideValue(email_service))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(get_unit_of_work, ProvideAsyncValue(fake_uow))
    dependency_overrides.set(get_settings, ProvideValue(settings))
    return app


@pytest_asyncio.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def async_client_with_fakes(
    app_with_fakes: FastAPI,
) -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_with_fakes)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client
