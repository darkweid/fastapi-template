from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field
import pytest

from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base


class SampleModel(Base):
    id: UUID
    created_at: datetime
    amount: Decimal


class AliasedModel(Base):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName")


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


def test_dumps_pydantic_model_uses_field_alias() -> None:
    # A response_model with an aliased field is re-validated by FastAPI using the
    # alias; dumping by field name would produce a payload that model rejects
    # once extra="forbid" is in play.
    serializer = JsonSerializer()
    model = AliasedModel(full_name="Ada Lovelace")

    raw = serializer.dumps(model)

    assert '"fullName": "Ada Lovelace"' in raw
    assert "full_name" not in raw
    assert serializer.loads(raw, AliasedModel) == model


def test_loads_without_model_returns_plain_payload() -> None:
    serializer = JsonSerializer()

    raw = serializer.dumps({"amount": Decimal("1.25")})

    assert serializer.loads(raw) == {"amount": "1.25"}


def test_dumps_rejects_unserializable_value() -> None:
    serializer = JsonSerializer()

    with pytest.raises(TypeError, match="cannot serialize"):
        serializer.dumps(object())
