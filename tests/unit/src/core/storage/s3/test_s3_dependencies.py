from __future__ import annotations

import pytest

from src.core.errors.exceptions import InfrastructureException
from src.core.storage.s3.dependencies import get_s3_adapter
from src.main.config import Config


async def test_get_s3_adapter_raises_when_disabled(settings: Config) -> None:
    assert settings.s3.S3_ENABLED is False

    generator = get_s3_adapter(settings)

    with pytest.raises(InfrastructureException, match="S3 is disabled"):
        await anext(generator)
