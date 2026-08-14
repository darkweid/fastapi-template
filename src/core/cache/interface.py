from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheKey:
    """
    Address of a cached value.

    The namespace is the entry's primary invalidation unit and the suffix names
    the entry inside it. Tags are additional invalidation units the entry answers
    to, and unlike the namespace they cut across namespaces: every entry tagged
    `users` dies when that tag is bumped, whichever user namespace it lives in.

    Tags travel inside the key rather than being passed to `set`, because a read
    resolves the same tag versions a write did - a value stored with a tag its
    reader does not declare would be unreachable forever.
    """

    namespace: str
    suffix: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Sorted and deduplicated so that two keys naming the same tags in
        # different order address the same entry and compare equal.
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))

    def __str__(self) -> str:
        tags = f"[{','.join(self.tags)}]" if self.tags else ""
        return f"{self.namespace}/{self.suffix}{tags}"


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

    async def invalidate_tags(self, *tags: str) -> None: ...

    async def get_or_set(
        self,
        key: CacheKey,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl: int | None = None,
        model: type[T] | None = None,
    ) -> T:
        """
        Return the cached value, or run factory and store its result.

        A stored `None` is indistinguishable from a miss here: a factory that
        legitimately returns `None` (a lookup annotated `-> X | None` that found
        nothing) runs on every call and rewrites `null` each time. Cache such a
        lookup only if that repeated call is acceptable, or have it return a
        sentinel value of its own instead of `None`.
        """
        ...
