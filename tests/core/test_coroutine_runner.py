import asyncio

import pytest

from src.core.utils.coroutine_runner import execute_coroutine_sync


async def _add(a: int, b: int) -> int:
    await asyncio.sleep(0.01)
    return a + b


async def _raise_error() -> None:
    await asyncio.sleep(0.01)
    raise ValueError("test error")


def test_execute_coroutine_sync_returns_result() -> None:
    """Base coroutine execution test."""
    result = execute_coroutine_sync(coroutine=_add(2, 3))
    assert result == 5


def test_execute_coroutine_sync_reuses_existing_open_loop() -> None:
    """
    if you have an existing open event loop, function should reuse it.
    """
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = execute_coroutine_sync(coroutine=_add(10, 20))
        assert result == 30
        assert not loop.is_closed()
    finally:
        asyncio.set_event_loop(previous_loop)
        loop.close()


def test_execute_coroutine_sync_creates_new_loop_if_closed() -> None:
    """
    if the existing event loop is closed,
    the function should create a new one and not reuse the closed one.
    """
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    old_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(old_loop)
    old_loop.close()

    result = execute_coroutine_sync(coroutine=_add(1, 2))
    assert result == 3

    new_loop = asyncio.get_event_loop()
    assert new_loop is not old_loop
    assert not new_loop.is_closed()

    try:
        new_loop.close()
    finally:
        asyncio.set_event_loop(previous_loop)


def test_execute_coroutine_sync_propagates_exceptions() -> None:
    """Exception propagation test."""
    with pytest.raises(ValueError) as exc_info:
        execute_coroutine_sync(coroutine=_raise_error())

    assert "test error" in str(exc_info.value)
