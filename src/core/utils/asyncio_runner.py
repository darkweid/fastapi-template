import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, overload

T = TypeVar("T")
CoroutineFactory = Callable[[], Coroutine[Any, Any, T]]


@overload
def run_coroutine_synchronously(*, coroutine: Coroutine[Any, Any, T]) -> T: ...


@overload
def run_coroutine_synchronously(*, coroutine: CoroutineFactory[T]) -> T: ...


def run_coroutine_synchronously(
    *, coroutine: Coroutine[Any, Any, T] | CoroutineFactory[T]
) -> T:
    """
    Run a coroutine in a proper event loop.

    - Reuse existing loop if it exists and is not closed.
    - If no loop exists, or it is closed, create a new one and set it as current.
    """
    coroutine_to_run = coroutine() if callable(coroutine) else coroutine

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coroutine_to_run)
