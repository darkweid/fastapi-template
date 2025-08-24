import pickle
from typing import Any

from starlette.templating import _TemplateResponse as TemplateResponse

from src.core.redis.cache.coder.interface import Coder


class PickleCoder(Coder):
    @classmethod
    def encode(cls, value: Any) -> bytes:
        """Encode a value into bytes for storage in the cache using pickle."""
        if isinstance(value, TemplateResponse):
            value = value.body
        return pickle.dumps(value)

    @classmethod
    def decode(cls, value: bytes) -> Any:
        """Decode bytes back into the original value from the cache using pickle."""
        return pickle.loads(value)  # noqa: S301
