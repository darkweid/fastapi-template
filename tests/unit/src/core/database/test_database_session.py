from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from src.core.database.session import get_session, get_unit_of_work


class FakeSessionContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None


class FakeSessionMaker:
    def __init__(self, value: object) -> None:
        self._value = value

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self._value)


FAKE_UOW = object()


async def fake_get_uow(_: object) -> object:
    return FAKE_UOW


@pytest.mark.asyncio
async def test_get_session_yields_session_from_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = object()
    fake_factory = FakeSessionMaker(fake_session)
    monkeypatch.setattr("src.core.database.session.async_session", fake_factory)

    session_generator: AsyncGenerator[object] = get_session()
    session = await session_generator.__anext__()

    assert session is fake_session
    await session_generator.aclose()


@pytest.mark.asyncio
async def test_get_unit_of_work_uses_get_uow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.core.database.session.get_uow", fake_get_uow)
    session = object()

    generator = get_unit_of_work(session=session)
    uow = await generator.__anext__()

    assert uow is FAKE_UOW
    await generator.aclose()
