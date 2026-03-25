from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import Response
import pytest
import redis.exceptions as redis_exc

from src.core.limiter import FastAPILimiter
import src.core.limiter.depends as limiter_depends
from src.core.limiter.depends import RateLimiter
from tests.fakes.redis import InMemoryRedis
from tests.helpers.requests import build_request


async def sample_endpoint() -> None:
    return None


class ErrorRedis:
    async def evalsha(self, *_args: object, **_kwargs: object) -> int:
        raise redis_exc.ConnectionError("down")


@pytest.fixture
def limiter_state() -> tuple[
    object | None,
    str | None,
    dict[str, object],
    int | None,
    int | None,
]:
    prev_redis = FastAPILimiter.redis
    prev_sha = FastAPILimiter.lua_sha
    prev_windows = RateLimiter._fallback_windows.copy()
    prev_degraded_since = RateLimiter._redis_degraded_since_ms
    prev_last_report_ms = RateLimiter._last_redis_degraded_report_ms
    RateLimiter._fallback_windows = {}
    RateLimiter._redis_degraded_since_ms = None
    RateLimiter._last_redis_degraded_report_ms = None
    yield (
        prev_redis,
        prev_sha,
        prev_windows,
        prev_degraded_since,
        prev_last_report_ms,
    )
    FastAPILimiter.redis = prev_redis
    FastAPILimiter.lua_sha = prev_sha
    RateLimiter._fallback_windows = prev_windows
    RateLimiter._redis_degraded_since_ms = prev_degraded_since
    RateLimiter._last_redis_degraded_report_ms = prev_last_report_ms


def test_rate_limiter_invalid_window_raises() -> None:
    with pytest.raises(ValueError, match="Rate limiter window must be greater than 0"):
        RateLimiter(times=1)


@pytest.mark.asyncio
async def test_rate_limiter_raises_when_not_initialized(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
) -> None:
    FastAPILimiter.redis = None
    FastAPILimiter.lua_sha = None
    limiter = RateLimiter(times=1, seconds=1)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    with pytest.raises(RuntimeError, match="FastAPILimiter must be initialized"):
        await limiter(request, response)


@pytest.mark.asyncio
async def test_rate_limiter_allows_request_when_under_limit(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    await limiter(request, response)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_calls_callback_on_limit(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    rate_key = await FastAPILimiter.identifier(request)
    key = f"{FastAPILimiter.prefix}:{rate_key}:{sample_endpoint.__name__}"
    fake_redis.set_evalsha_result(key, 1000)

    await limiter(request, response)

    callback.assert_awaited_once_with(request, response, 1000)


@pytest.mark.asyncio
async def test_rate_limiter_loads_script_when_missing(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = "missing"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    await limiter(request, response)

    assert FastAPILimiter.lua_sha in fake_redis._scripts
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_uses_in_memory_fallback_on_redis_error(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FastAPILimiter.redis = ErrorRedis()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    current_time_ms = 1_000
    monkeypatch.setattr(
        limiter_depends,
        "_current_time_ms",
        lambda: current_time_ms,
    )

    await limiter(request, response)
    callback.assert_not_awaited()

    await limiter(request, response)
    callback.assert_awaited_once_with(request, response, 1000)


@pytest.mark.asyncio
async def test_rate_limiter_fallback_window_resets_after_expiry(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FastAPILimiter.redis = ErrorRedis()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    current_time_ms = 1_000

    def fake_now_ms() -> int:
        return current_time_ms

    monkeypatch.setattr(limiter_depends, "_current_time_ms", fake_now_ms)

    await limiter(request, response)
    current_time_ms = 2_001
    await limiter(request, response)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_allows_request_when_fallback_itself_fails(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FastAPILimiter.redis = ErrorRedis()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()
    fallback_error = RuntimeError("memory limiter broken")
    error_mock = Mock()

    monkeypatch.setattr(
        limiter,
        "_check_limit_in_memory",
        Mock(side_effect=fallback_error),
    )
    monkeypatch.setattr(limiter_depends.logger, "error", error_mock)

    await limiter(request, response)

    callback.assert_not_awaited()
    assert error_mock.call_count == 2
    assert "security-significant incident" in error_mock.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_rate_limiter_reports_sentry_on_fallback_activation_with_cooldown(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FastAPILimiter.redis = ErrorRedis()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    capture_message_mock = Mock()
    limiter = RateLimiter(times=10, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    current_time_ms = 1_000

    def fake_now_ms() -> int:
        return current_time_ms

    monkeypatch.setattr(limiter_depends, "_current_time_ms", fake_now_ms)
    monkeypatch.setattr(
        limiter_depends.sentry_sdk,
        "capture_message",
        capture_message_mock,
    )

    await limiter(request, response)
    await limiter(request, response)
    current_time_ms += RateLimiter._fallback_sentry_cooldown_ms + 1
    await limiter(request, response)

    callback.assert_not_awaited()
    assert capture_message_mock.call_count == 2
    first_call = capture_message_mock.call_args_list[0]
    second_call = capture_message_mock.call_args_list[1]
    assert "In-memory fallback limiter is active" in first_call.args[0]
    assert first_call.kwargs["level"] == "error"
    assert "In-memory fallback limiter is active" in second_call.args[0]


@pytest.mark.asyncio
async def test_rate_limiter_reports_sentry_on_redis_recovery(
    limiter_state: tuple[
        object | None, str | None, dict[str, object], int | None, int | None
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FastAPILimiter.redis = object()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    capture_message_mock = Mock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    current_time_ms = 1_000

    def fake_now_ms() -> int:
        return current_time_ms

    monkeypatch.setattr(limiter_depends, "_current_time_ms", fake_now_ms)
    monkeypatch.setattr(
        limiter_depends.sentry_sdk,
        "capture_message",
        capture_message_mock,
    )
    eval_redis_limit_mock = AsyncMock(
        side_effect=[
            redis_exc.ConnectionError("down"),
            0,
        ]
    )
    monkeypatch.setattr(limiter, "_eval_redis_limit", eval_redis_limit_mock)

    await limiter(request, response)
    current_time_ms = 1_500
    await limiter(request, response)

    callback.assert_not_awaited()
    assert capture_message_mock.call_count == 2
    assert (
        "In-memory fallback limiter is active"
        in capture_message_mock.call_args_list[0].args[0]
    )
    assert capture_message_mock.call_args_list[0].kwargs["level"] == "error"
    assert "Redis limiter recovered" in capture_message_mock.call_args_list[1].args[0]
    assert "Downtime: 500ms" in capture_message_mock.call_args_list[1].args[0]
    assert capture_message_mock.call_args_list[1].kwargs["level"] == "info"
