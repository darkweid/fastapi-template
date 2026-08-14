from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base


class SampleModel(Base):
    id: UUID
    created_at: datetime
    amount: Decimal


def test_dumps_and_loads_plain_payload() -> None:
    serializer = JsonSerializer()

    raw = serializer.dumps({"a": 1, "b": ["x"]})

    assert serializer.loads(raw) == {"a": 1, "b": ["x"]}


def test_dumps_pydantic_model_and_loads_typed() -> None:
    serializer = JsonSerializer()
    model = SampleModel(
        id=uuid4(),
        created_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        amount=Decimal("10.50"),
    )

    raw = serializer.dumps(model)
    restored = serializer.loads(raw, SampleModel)

    assert restored == model
    assert isinstance(restored.amount, Decimal)


def test_loads_without_model_returns_plain_payload() -> None:
    serializer = JsonSerializer()

    raw = serializer.dumps({"amount": Decimal("1.25")})

    assert serializer.loads(raw) == {"amount": "1.25"}


def test_dumps_rejects_unserializable_value() -> None:
    serializer = JsonSerializer()

    with pytest.raises(TypeError, match="cannot serialize"):
        serializer.dumps(object())
