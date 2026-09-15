from unittest.mock import AsyncMock

import pytest

from src.core.pagination import PaginatedResponse
from src.event_log.dependencies import get_event_log_service
from src.event_log.enums import ActorType
from src.user.auth.dependencies import get_current_user
from src.user.enums import UserRole
from tests.factories.user_factory import build_user
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue

EMPTY_PAGE: PaginatedResponse = PaginatedResponse(
    items=[], total=0, page=1, size=50, pages=0
)


@pytest.mark.asyncio
async def test_the_log_is_closed_to_a_role_without_view_logs(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    """The log holds every other user's actions; reading it is its own right."""
    dependency_overrides.set(
        get_current_user, ProvideValue(build_user(role=UserRole.VIEWER))
    )
    service = AsyncMock()
    dependency_overrides.set(get_event_log_service, ProvideValue(service))

    response = await async_client_with_fakes.get("/v1/event-logs/")

    assert response.status_code == 403
    service.get_paginated_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_filters_reach_the_repository_as_exact_matches(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    """A filter silently dropped here reads as "no such events happened"."""
    dependency_overrides.set(
        get_current_user, ProvideValue(build_user(role=UserRole.ADMIN))
    )
    service = AsyncMock()
    service.get_paginated_list = AsyncMock(return_value=EMPTY_PAGE)
    dependency_overrides.set(get_event_log_service, ProvideValue(service))

    response = await async_client_with_fakes.get(
        "/v1/event-logs/?actor_type=user&event_type=note.created"
    )

    assert response.status_code == 200
    call = service.get_paginated_list.await_args
    # `Base` is configured with `use_enum_values`, so the parameter arrives as
    # the enum's value; SQLAlchemy resolves either form against the column.
    assert call.kwargs["actor_type"] == ActorType.USER
    assert call.kwargs["event_type"] == "note.created"
    assert call.kwargs["query"].order_by is None


@pytest.mark.asyncio
async def test_an_unknown_filter_is_rejected_instead_of_ignored(
    async_client_with_fakes, dependency_overrides: DependencyOverrides
) -> None:
    """A misspelled filter that returns the whole log is worse than an error."""
    dependency_overrides.set(
        get_current_user, ProvideValue(build_user(role=UserRole.ADMIN))
    )
    service = AsyncMock()
    dependency_overrides.set(get_event_log_service, ProvideValue(service))

    response = await async_client_with_fakes.get("/v1/event-logs/?unsupported=value")

    assert response.status_code == 422
    service.get_paginated_list.assert_not_awaited()
