"""
Live smoke found two framework-raised HTTPExceptions that bypassed the error
contract entirely: FastAPI's Security utilities answer missing credentials
with a bare {"detail": "Not authenticated"} body, and Starlette's router
answers an unmatched path with {"detail": "Not Found"}. Both must come back
as the flat {"code", "message"} contract like every other error.

Uses the plain `async_client` fixture (real `app`, no dependency overrides)
because `app_with_fakes` overrides get_session/get_redis_client/etc. but not
the security dependency itself - GET /v1/users/me's only dependency is
`get_authenticated_user`, whose first sub-dependency is the Security(header)
check, so it fails before touching the database and needs no overrides.
"""

import httpx2
import pytest


@pytest.mark.asyncio
async def test_unmatched_route_answers_the_error_contract(
    async_client: httpx2.AsyncClient,
) -> None:
    response = await async_client.get("/nonexistent")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Not Found"}


@pytest.mark.asyncio
async def test_missing_credentials_answers_the_error_contract(
    async_client: httpx2.AsyncClient,
) -> None:
    response = await async_client.get("/v1/users/me")

    assert response.status_code == 401
    assert response.json() == {"code": "unauthorized", "message": "Not authenticated"}
