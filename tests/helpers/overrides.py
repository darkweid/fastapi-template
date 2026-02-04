from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI


class DependencyOverrides:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._original: dict[Callable[..., Any], Callable[..., Any] | None] = {}

    def set(
        self, dependency: Callable[..., Any], override: Callable[..., Any]
    ) -> None:
        if dependency not in self._original:
            self._original[dependency] = self._app.dependency_overrides.get(dependency)
        self._app.dependency_overrides[dependency] = override

    def reset(self) -> None:
        for dependency, original in self._original.items():
            if original is None:
                self._app.dependency_overrides.pop(dependency, None)
            else:
                self._app.dependency_overrides[dependency] = original
        self._original.clear()
