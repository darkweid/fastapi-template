from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx2
import pytest

from src.core.database.session import get_unit_of_work
from src.core.outbox.dependencies import get_task_dispatcher
from src.main.config import config
from src.user.auth.tasks import send_reset_password_email_task
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.providers import ProvideAsyncValue, ProvideValue

AUTH_SOURCE_DIR = Path(__file__).resolve().parents[5] / "src" / "user" / "auth"


class FakeUsersRepository:
    def __init__(self, user: object) -> None:
        self.get_single = AsyncMock(return_value=user)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.mark.asyncio
async def test_forged_host_does_not_reach_the_reset_link(
    app_with_fakes: FastAPI,
    dependency_overrides,
    fake_session: FakeAsyncSession,
) -> None:
    """
    Given: a password reset request carrying an attacker-controlled Host header.
    When: the endpoint queues the reset email.
    Then: the queued task carries no host taken from the request, so the link in
          the email can only be built from PUBLIC_BASE_URL.
    """
    user = build_user(email="user@example.com")
    uow = FakeUnitOfWork(
        session=fake_session,
        repositories={"users": FakeUsersRepository(user)},
    )
    dispatcher = AsyncMock()
    dependency_overrides.set(get_unit_of_work, ProvideAsyncValue(uow))
    dependency_overrides.set(get_task_dispatcher, ProvideValue(dispatcher))

    transport = httpx2.ASGITransport(app=app_with_fakes)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/v1/users/auth/password/reset",
            json={"email": user.email},
            headers={"Host": "evil.com"},
        )

    assert response.status_code == 200
    queued = dispatcher.enqueue_transactional.await_args.args
    assert queued[1] is send_reset_password_email_task
    assert not any("evil.com" in str(argument) for argument in queued[2:])


def test_auth_module_builds_no_links_from_the_request() -> None:
    """
    Guards the fix itself: a link assembled from request.base_url is exactly the
    poisoning bug, and it reappears the moment someone reaches for the request
    again instead of PUBLIC_BASE_URL.
    """
    offenders = [
        path.name
        for path in AUTH_SOURCE_DIR.rglob("*.py")
        if "base_url" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_reset_link_origin_comes_from_configuration() -> None:
    assert config.app.PUBLIC_BASE_URL.startswith("http")
    assert config.app.PASSWORD_RESET_PATH.startswith("/")
    assert config.app.EMAIL_VERIFY_PATH.startswith("/")
