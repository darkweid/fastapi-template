from __future__ import annotations

import datetime
from decimal import Decimal

from fastapi.responses import JSONResponse
import pytest

from src.core.redis.cache.coder.json_coder import JsonCoder
from src.core.redis.cache.coder.pickle_coder import PickleCoder


def test_json_coder_roundtrip_for_supported_types() -> None:
    payload = {
        "date": datetime.date(2024, 1, 1),
        "datetime": datetime.datetime(2024, 1, 1, 12, 0, 0),
        "decimal": Decimal("10.5"),
        "value": "ok",
    }

    encoded = JsonCoder.encode(payload)
    decoded = JsonCoder.decode(encoded)

    assert decoded["date"] == payload["date"]
    assert decoded["datetime"] == payload["datetime"]
    assert decoded["decimal"] == payload["decimal"]
    assert decoded["value"] == "ok"


def test_json_coder_encodes_json_response_body() -> None:
    response = JSONResponse({"status": "ok"})

    encoded = JsonCoder.encode(response)
    decoded = JsonCoder.decode(encoded)

    assert decoded == {"status": "ok"}


def test_json_coder_raises_on_unknown_spec_type() -> None:
    encoded = b'{"val":"x","_spec_type":"unknown"}'

    with pytest.raises(TypeError, match="Unknown"):
        JsonCoder.decode(encoded)


def test_pickle_coder_roundtrip() -> None:
    payload = {"value": 123, "nested": {"a": "b"}}

    encoded = PickleCoder.encode(payload)
    decoded = PickleCoder.decode(encoded)

    assert decoded == payload
