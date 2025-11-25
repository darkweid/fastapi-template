from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast, overload

from celery import shared_task

from celery_tasks.main import celery_app  # noqa: F401


class CeleryTask(Protocol):
    """Protocol for a Celery task with delay method."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def delay(self, *args: Any, **kwargs: Any) -> Any: ...


F = TypeVar("F", bound=Callable[..., Any])


@overload
def typed_shared_task(func: F) -> F: ...


@overload
def typed_shared_task(
    *, name: str | None = None, **kwargs: Any
) -> Callable[[F], F]: ...


def typed_shared_task(func: F | None = None, **kwargs: Any) -> F | Callable[[F], F]:
    """A wrapper for shared_task that preserves type information and adds .delay().

    Can be used as:
    @typed_shared_task
    def task_function():
        pass

    Or with parameters:
    @typed_shared_task(name='explicit_task_name')
    def task_function():
        pass
    """

    def decorator(func: F) -> F:
        return cast(F, shared_task(**kwargs)(func))

    if func is None:
        return decorator
    return decorator(func)
