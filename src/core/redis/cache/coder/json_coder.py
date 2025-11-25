from collections.abc import Callable
import datetime
from decimal import Decimal
import json
from typing import (
    Any,
)

from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from src.core.redis.cache.coder.interface import Coder

CONVERTERS: dict[str, Callable[[str], Any]] = {
    "date": lambda x: datetime.date.fromisoformat(x),
    "datetime": lambda x: datetime.datetime.fromisoformat(x),
    "decimal": Decimal,
}


class JsonEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, datetime.datetime):
            return {"val": str(o), "_spec_type": "datetime"}
        elif isinstance(o, datetime.date):
            return {"val": str(o), "_spec_type": "date"}
        elif isinstance(o, Decimal):
            return {"val": str(o), "_spec_type": "decimal"}
        else:
            return jsonable_encoder(o)


def object_hook(obj: dict[str, Any]) -> Any:
    _spec_type = obj.get("_spec_type")
    if not _spec_type:
        return obj

    if _spec_type in CONVERTERS:
        return CONVERTERS[_spec_type](obj["val"])
    else:
        raise TypeError(f"Unknown {_spec_type}")


class JsonCoder(Coder):
    @classmethod
    def encode(cls, value: Any) -> bytes:
        """Encode a value into bytes for storage in the cache using json."""
        if isinstance(value, JSONResponse):
            body = value.body
            if isinstance(body, memoryview):
                return bytes(body)
            return body
        return json.dumps(value, cls=JsonEncoder).encode()

    @classmethod
    def decode(cls, value: bytes) -> Any:
        """Decode bytes back into the original value from the cache using json."""
        return json.loads(value.decode(), object_hook=object_hook)
