from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.user.auth.token_transport import TokenTransport, get_token_transport


@pytest.fixture
def transport_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(
        transport: TokenTransport = Depends(get_token_transport),
    ) -> dict[str, str]:
        return {"transport": transport.value}

    return app


@pytest.mark.asyncio
async def test_missing_header_defaults_to_cookie(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get("/probe")

    assert response.json() == {"transport": "cookie"}


@pytest.mark.asyncio
async def test_body_transport_is_selected_by_header(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get("/probe", headers={"X-Token-Transport": "body"})

    assert response.json() == {"transport": "body"}


@pytest.mark.asyncio
async def test_unknown_transport_value_is_rejected(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/probe", headers={"X-Token-Transport": "carrier-pigeon"}
        )

    assert response.status_code == 422
