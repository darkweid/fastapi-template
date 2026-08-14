from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Address of a cached value: namespace is the invalidation unit, suffix is the entry."""

    namespace: str
    suffix: str

    def __str__(self) -> str:
        return f"{self.namespace}/{self.suffix}"


class CacheScope(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class Serializer(Protocol):
    def dumps(self, value: Any) -> str: ...

    def loads(self, raw: str, model: type[T] | None = None) -> Any: ...


class Cache(Protocol):
    async def get(self, key: CacheKey, *, model: type[T] | None = None) -> Any: ...

    async def set(
        self, key: CacheKey, value: Any, *, ttl: int | None = None
    ) -> None: ...

    async def get_raw(self, key: CacheKey) -> str | None: ...

    async def set_raw(
        self, key: CacheKey, raw: str, *, ttl: int | None = None
    ) -> None: ...

    async def delete(self, key: CacheKey) -> None: ...

    async def invalidate(self, namespace: str) -> None: ...

    async def get_or_set(
        self,
        key: CacheKey,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl: int | None = None,
        model: type[T] | None = None,
    ) -> T: ...
