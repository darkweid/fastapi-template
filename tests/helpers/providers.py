from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Generic, TypeVar

from tests.fakes.db import FakeUnitOfWork

T = TypeVar("T")


class ProvideValue(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    def __call__(self) -> T:
        return self._value


class ProvideAsyncValue(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    async def __call__(self) -> AsyncGenerator[T]:
        yield self._value


class ProvideFreshUnitOfWork:
    """One unit per request, as the real `get_unit_of_work`: a completed unit
    refuses a second entry, so a shared one would fail the second request."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        self._uow = uow

    async def __call__(self) -> AsyncGenerator[FakeUnitOfWork]:
        yield self._uow.fresh()
