from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

T = TypeVar("T")

# functools.lru_cache's stub types __call__ as accepting only Hashable, which
# type[Any] does not satisfy under strict mypy - a plain dict cache sidesteps that.
_adapters: dict[type[Any], TypeAdapter[Any]] = {}


def _adapter(model: type[Any]) -> TypeAdapter[Any]:
    if model not in _adapters:
        _adapters[model] = TypeAdapter(model)
    return _adapters[model]


def _encode_unknown(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(f"Cache cannot serialize {type(value).__name__}")


class JsonSerializer:
    """JSON codec for cache values with optional typed decoding."""

    def dumps(self, value: Any) -> str:
        return json.dumps(value, default=_encode_unknown)

    def loads(self, raw: str, model: type[T] | None = None) -> Any:
        # Without `model`, non-string types (Decimal, UUID, datetime) come back as
        # plain strings - typed decoding is required whenever the caller expects them back.
        payload = json.loads(raw)
        if model is None:
            return payload
        return _adapter(model).validate_python(payload)
