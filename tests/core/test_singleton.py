from __future__ import annotations

from src.core.patterns.singleton import singleton


@singleton
class SampleSingleton:
    def __init__(self, value: int) -> None:
        self.value = value


@singleton
class AnotherSingleton:
    def __init__(self, value: int) -> None:
        self.value = value


def test_singleton_returns_same_instance() -> None:
    first = SampleSingleton(1)
    second = SampleSingleton(2)

    assert first is second
    assert second.value == 1


def test_singletons_are_isolated_per_class() -> None:
    first = SampleSingleton(1)
    other = AnotherSingleton(3)

    assert first is not other
    assert other.value == 3
