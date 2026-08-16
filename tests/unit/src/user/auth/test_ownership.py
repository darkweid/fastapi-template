from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.session import get_session
from src.user.auth.dependencies import get_current_user
from src.user.dependencies import get_user_service
from src.user.enums import UserRole
from tests.factories.user_factory import build_user
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUserService:
    def __init__(self, user) -> None:
        self.get_single = AsyncMock(return_value=user)
        self.get_single_or_404 = AsyncMock()
        if user is None:
            from src.core.errors.exceptions import InstanceNotFoundException

            self.get_single_or_404.side_effect = InstanceNotFoundException(
                "User not found"
            )
        else:
            self.get_single_or_404.return_value = user


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.mark.asyncio
async def test_viewer_reads_own_summary(
    async_client, dependency_overrides, fake_session
) -> None:
    viewer = build_user(role=UserRole.VIEWER)
    dependency_overrides.set(get_current_user, ProvideValue(viewer))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(viewer)))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    response = await async_client.get(f"/v1/users/{viewer.id}")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_probe_a_foreign_id(
    async_client, dependency_overrides, fake_session
) -> None:
    viewer = build_user(role=UserRole.VIEWER)
    foreign = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(viewer))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(foreign)))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    response = await async_client.get(f"/v1/users/{foreign.id}")

    # The body must be indistinguishable from a genuinely missing user.
    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "User not found."}


@pytest.mark.asyncio
async def test_editor_reads_a_foreign_summary_via_permission(
    async_client, dependency_overrides, fake_session
) -> None:
    editor = build_user(role=UserRole.EDITOR)  # has VIEW_USERS
    foreign = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(editor))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(foreign)))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    response = await async_client.get(f"/v1/users/{foreign.id}")

    assert response.status_code == 200
