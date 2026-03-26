from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from src.core.utils.retry import with_retries, with_retries_on_result


class SyncCounter:
    def __init__(self, fail_times: int, result: int) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.result = result

    def run(self) -> int:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ValueError("fail")
        return self.result


class AsyncCounter:
    def __init__(self, fail_times: int, result: int) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.result = result

    async def run(self) -> int:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ValueError("fail")
        return self.result


class AsyncResultCounter:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.calls = 0
        self.results = results

    async def run(self) -> dict[str, object]:
        result = self.results[self.calls]
        self.calls += 1
        return result


def test_with_retries_sync_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_mock = Mock()
    monkeypatch.setattr("src.core.utils.retry.time.sleep", sleep_mock)

    counter = SyncCounter(fail_times=2, result=7)
    wrapped = with_retries(max_retries=3, delay=1)(counter.run)

    result = wrapped()

    assert result == 7
    assert counter.calls == 3
    assert sleep_mock.call_count == 2


@pytest.mark.asyncio
async def test_with_retries_async_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("src.core.utils.retry.asyncio.sleep", sleep_mock)

    counter = AsyncCounter(fail_times=1, result=5)
    wrapped = with_retries(max_retries=2, delay=1)(counter.run)

    result = await wrapped()

    assert result == 5
    assert counter.calls == 2
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_retries_async_raises_after_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("src.core.utils.retry.asyncio.sleep", sleep_mock)

    counter = AsyncCounter(fail_times=3, result=1)
    wrapped = with_retries(max_retries=2, delay=1)(counter.run)

    with pytest.raises(ValueError, match="fail"):
        await wrapped()

    assert counter.calls == 2
    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_retries_on_result_retries_until_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("src.core.utils.retry.asyncio.sleep", sleep_mock)

    counter = AsyncResultCounter(
        results=[
            {"result": {"code": "FAIL"}},
            {"result": {"code": "OK"}},
        ]
    )
    wrapped = with_retries_on_result(max_retries=2, delay=1)(counter.run)

    result = await wrapped()

    assert result["result"]["code"] == "OK"
    assert counter.calls == 2
    sleep_mock.assert_awaited_once()
