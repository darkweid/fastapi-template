from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.errors.exceptions import InfrastructureException
from src.core.storage.s3.dependencies import get_s3_adapter
from src.main.config import Config


def _fake_request(s3_adapter: object | None = None) -> SimpleNamespace:
    state = (
        SimpleNamespace(s3_adapter=s3_adapter)
        if s3_adapter is not None
        else SimpleNamespace()
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_get_s3_adapter_raises_when_disabled(settings: Config) -> None:
    assert settings.s3.S3_ENABLED is False

    with pytest.raises(InfrastructureException, match="S3 is disabled"):
        await get_s3_adapter(_fake_request(), settings)


@pytest.mark.asyncio
async def test_get_s3_adapter_returns_the_app_state_adapter_when_enabled(
    settings: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.s3, "S3_ENABLED", True)
    fake_adapter = object()

    result = await get_s3_adapter(_fake_request(fake_adapter), settings)

    assert result is fake_adapter
