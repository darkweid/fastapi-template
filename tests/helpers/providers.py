from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Generic, TypeVar

T = TypeVar("T")


class ProvideValue(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    def __call__(self) -> T:
        return self._value


class ProvideAsyncValue(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    async def __call__(self) -> AsyncGenerator[T, None]:
        yield self._value
