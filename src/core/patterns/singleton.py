from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

T = TypeVar("T")


def singleton(cls: type[T]) -> Callable[..., T]:
    """
    A decorator to make a class a singleton
    """
    instances: dict[type[Any], Any] = {}

    @wraps(cls)
    def get_instance(*args: Any, **kwargs: Any) -> T:
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return cast(T, instances[cls])

    return get_instance
