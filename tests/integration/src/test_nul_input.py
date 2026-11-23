"""A NUL is valid JSON and a valid query string, and PostgreSQL refuses it: one
that gets past the schema answers 500."""

from fastapi import FastAPI
import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.user.auth.dependencies import get_current_user
from src.user.enums import UserRole
from tests.factories.user_factory import build_user
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/notes/", {"title": "Title", "content": "a\x00b"}),
        ("POST", "/v1/notes/", {"title": "a\x00b"}),
        ("GET", "/v1/notes/?search=a%00b", None),
        ("GET", "/v1/event-logs/?event_type=a%00b", None),
    ],
    ids=["content", "title", "search", "event_type"],
)
async def test_a_nul_answers_422(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    db_session: AsyncSession,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    admin = build_user(role=UserRole.ADMIN)
    dependency_overrides.set(get_session, ProvideAsyncValue(db_session))
    dependency_overrides.set(get_current_user, ProvideValue(admin))
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 422
