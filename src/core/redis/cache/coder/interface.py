from abc import ABC, abstractmethod
from typing import Any


class Coder(ABC):
    @classmethod
    @abstractmethod
    def encode(cls, value: Any) -> bytes:
        """Encode a value into bytes for storage in the cache."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def decode(cls, value: bytes) -> Any:
        """Decode bytes back into the original value from the cache."""
        raise NotImplementedError
