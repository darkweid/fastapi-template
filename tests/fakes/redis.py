from __future__ import annotations

import asyncio
from collections.abc import Callable
import fnmatch
import hashlib
import time
from typing import Any

import redis.exceptions as redis_exc

from src.core.auth.redis_scripts import (
    CONSUME_CHALLENGE_SCRIPT,
    ROTATE_REFRESH_TOKEN_SCRIPT,
)
from src.core.cache.redis_scripts import (
    CACHE_DELETE_SCRIPT,
    CACHE_GET_SCRIPT,
    CACHE_INVALIDATE_SCRIPT,
    CACHE_SET_SCRIPT,
)


def _normalize_key(key: str | bytes) -> str:
    if isinstance(key, bytes):
        return key.decode("utf-8")
    return key


def _normalize_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _now() -> float:
    return time.monotonic()


async def _round_trip() -> None:
    """Yield the way a real command does, so concurrent callers interleave.

    Without this every method here runs start to finish before the event loop
    looks at anyone else, and a race test passes against an implementation that
    reads and writes in two round trips - which is the bug those tests exist to
    catch. Script bodies must not call this: Redis runs a script as one unit.
    """
    await asyncio.sleep(0)


class InMemoryRedis:
    def __init__(self) -> None:
        # Wall clock behind TIME/time(): tests pin it to a fixed value so the
        # refresh grace-window math never races the real clock. Key expiry
        # stays on time.monotonic() and is unaffected by reassigning this.
        self.wall_clock: Callable[[], float] = time.time
        self._store: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._expires: dict[str, float] = {}
        self._scripts: dict[str, str] = {}
        self._evalsha_overrides: dict[str, int] = {}
        # Keys every evalsha ran against, in order. The rate limiter is the only
        # evalsha user, so this doubles as a record of which limiter windows a
        # request actually consumed.
        self.evalsha_keys: list[str] = []
        self.closed = False
        self.cache_eval_calls = 0
        self._failures = 0
        self._failure_error: Exception = redis_exc.ConnectionError("fake redis down")

    def set_evalsha_result(self, key: str, result: int) -> None:
        self._evalsha_overrides[key] = result

    def clear_evalsha_overrides(self) -> None:
        self._evalsha_overrides.clear()

    def fail_next_commands(
        self, count: int = 1, *, error: Exception | None = None
    ) -> None:
        self._failures = count
        self._failure_error = error or redis_exc.ConnectionError("fake redis down")

    def _purge_expired(self, key: str) -> None:
        expires_at = self._expires.get(key)
        if expires_at is None:
            return
        if _now() >= expires_at:
            self._store.pop(key, None)
            self._zsets.pop(key, None)
            self._expires.pop(key, None)

    def _read(self, key: str) -> str | None:
        """The store as a script sees it: no yield, no round trip."""
        self._purge_expired(key)
        return self._store.get(key)

    def _write(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self._store[key] = value
        if ttl_seconds is not None:
            self._expires[key] = _now() + int(ttl_seconds)

    def _drop(self, key: str) -> int:
        self._purge_expired(key)
        if key not in self._store and key not in self._zsets:
            return 0
        self._store.pop(key, None)
        self._zsets.pop(key, None)
        self._expires.pop(key, None)
        return 1

    def _expire(self, key: str, seconds: int) -> None:
        if key in self._store or key in self._zsets:
            self._expires[key] = _now() + int(seconds)

    async def get(self, key: str | bytes) -> str | None:
        await _round_trip()
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        return self._store.get(key_norm)

    async def set(
        self,
        key: str | bytes,
        value: Any,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool:
        await _round_trip()
        key_norm = _normalize_key(key)
        if nx:
            self._purge_expired(key_norm)
            if key_norm in self._store:
                return False
        self._store[key_norm] = _normalize_value(value)
        if ex is not None:
            self._expires[key_norm] = _now() + int(ex)
        elif px is not None:
            self._expires[key_norm] = _now() + (int(px) / 1000)
        else:
            self._expires.pop(key_norm, None)
        return True

    async def setex(self, key: str | bytes, time_seconds: int, value: Any) -> bool:
        await _round_trip()
        return await self.set(key, value, ex=time_seconds)

    async def delete(self, *keys: str | bytes) -> int:
        await _round_trip()
        deleted = 0
        for key in keys:
            key_norm = _normalize_key(key)
            self._purge_expired(key_norm)
            if key_norm in self._store or key_norm in self._zsets:
                self._store.pop(key_norm, None)
                self._zsets.pop(key_norm, None)
                self._expires.pop(key_norm, None)
                deleted += 1
        return deleted

    async def exists(self, key: str | bytes) -> int:
        await _round_trip()
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        return int(key_norm in self._store or key_norm in self._zsets)

    async def expire(self, key: str | bytes, seconds: int, *, nx: bool = False) -> bool:
        await _round_trip()
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        if key_norm not in self._store and key_norm not in self._zsets:
            return False
        if nx and key_norm in self._expires:
            return False
        self._expires[key_norm] = _now() + int(seconds)
        return True

    async def incr(self, key: str | bytes) -> int:
        await _round_trip()
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        # Writes the store directly: set() would drop the TTL, but Redis INCR
        # preserves it.
        value = int(self._store.get(key_norm, "0")) + 1
        self._store[key_norm] = str(value)
        return value

    async def zadd(self, key: str | bytes, mapping: dict[str, float]) -> int:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        target = self._zsets.setdefault(key_norm, {})
        added = 0
        for member, score in mapping.items():
            member_norm = _normalize_value(member)
            if member_norm not in target:
                added += 1
            target[member_norm] = float(score)
        return added

    async def zrem(self, key: str | bytes, *members: str | bytes) -> int:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        target = self._zsets.get(key_norm)
        if target is None:
            return 0
        removed = 0
        for member in members:
            member_norm = _normalize_value(member)
            if member_norm in target:
                target.pop(member_norm)
                removed += 1
        if not target:
            self._zsets.pop(key_norm, None)
            self._expires.pop(key_norm, None)
        return removed

    async def zrange(self, key: str | bytes, start: int, end: int) -> list[str]:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        members = [
            member
            for member, _score in sorted(
                self._zsets.get(key_norm, {}).items(),
                key=lambda item: (item[1], item[0]),
            )
        ]
        # Redis treats `end` as inclusive, with -1 meaning the last member.
        return members[start : end + 1 if end != -1 else None]

    async def zremrangebyscore(
        self, key: str | bytes, min_score: float, max_score: float
    ) -> int:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        target = self._zsets.get(key_norm)
        if target is None:
            return 0
        doomed = [
            member
            for member, score in target.items()
            if float(min_score) <= score <= float(max_score)
        ]
        for member in doomed:
            target.pop(member)
        if not target:
            self._zsets.pop(key_norm, None)
            self._expires.pop(key_norm, None)
        return len(doomed)

    async def zscore(self, key: str | bytes, member: str) -> float | None:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        return self._zsets.get(key_norm, {}).get(_normalize_value(member))

    async def ttl(self, key: str | bytes) -> int:
        await _round_trip()
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        if key_norm not in self._store and key_norm not in self._zsets:
            return -2
        expires_at = self._expires.get(key_norm)
        if expires_at is None:
            return -1
        # round(), not int(): truncation would report one second short whenever
        # a fraction of a millisecond elapses between set() and this call.
        return max(0, round(expires_at - _now()))

    async def scan(
        self,
        cursor: int = 0,
        match: str | None = None,
        count: int | None = None,
    ) -> tuple[int, list[str]]:
        for key in list(self._store.keys()):
            self._purge_expired(key)

        keys = list(self._store.keys())
        if match:
            keys = [key for key in keys if fnmatch.fnmatch(key, match)]
        if count is not None:
            keys = keys[:count]
        return 0, keys

    async def script_load(self, script: str) -> str:
        sha = hashlib.sha1(script.encode("utf-8")).hexdigest()
        self._scripts[sha] = script
        return sha

    async def evalsha(
        self,
        sha: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> int:
        if sha not in self._scripts:
            raise redis_exc.NoScriptError(
                "NOSCRIPT No matching script. Please use EVAL."
            )

        keys = [_normalize_key(key) for key in keys_and_args[:numkeys]]
        key = keys[0] if keys else ""
        self.evalsha_keys.append(key)
        if key in self._evalsha_overrides:
            return self._evalsha_overrides[key]
        return 0

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        if self._failures > 0:
            self._failures -= 1
            raise self._failure_error

        normalized = script.strip()
        if normalized == ROTATE_REFRESH_TOKEN_SCRIPT.strip():
            return self._eval_rotate_refresh_token(numkeys, *keys_and_args)
        if normalized == CONSUME_CHALLENGE_SCRIPT.strip():
            return self._eval_consume_challenge(numkeys, *keys_and_args)

        # The cache scripts take a variable number of key arguments - one version
        # counter for the namespace plus one per tag - so the split follows numkeys
        # exactly as Redis does, not a fixed position.
        counters = [_normalize_key(key) for key in keys_and_args[:numkeys]]
        args = keys_and_args[numkeys:]
        if normalized == CACHE_GET_SCRIPT.strip():
            return self._eval_cache_get(counters, *args)
        if normalized == CACHE_SET_SCRIPT.strip():
            return self._eval_cache_set(counters, *args)
        if normalized == CACHE_DELETE_SCRIPT.strip():
            return self._eval_cache_delete(counters, *args)
        if normalized == CACHE_INVALIDATE_SCRIPT.strip():
            return self._eval_cache_invalidate(counters, *args)
        raise NotImplementedError("Script not supported in fake Redis.")

    # Every script body below is synchronous, and a new one must be too: an
    # await between the read that decides and the write that acts would let two
    # callers interleave where real Redis - which runs a script as one unit -
    # cannot. A race test would then pass against an implementation that does
    # not hold, and a cache test would see a version counter Redis can never
    # produce. They reach the store through _read/_write/_drop/_expire for the
    # same reason: the public commands yield.
    def _cache_value_key(self, counters: list[str], prefix_ns: str, suffix: str) -> str:
        versions = [self._read(counter) or "0" for counter in counters]
        return f"{prefix_ns}:v{'.'.join(versions)}:{suffix}"

    def _eval_cache_get(self, counters: list[str], *args: Any) -> str | None:
        self.cache_eval_calls += 1
        key = self._cache_value_key(
            counters,
            _normalize_value(args[0]),
            _normalize_value(args[1]),
        )
        return self._read(key)

    def _eval_cache_set(self, counters: list[str], *args: Any) -> int:
        self.cache_eval_calls += 1
        key = self._cache_value_key(
            counters,
            _normalize_value(args[0]),
            _normalize_value(args[1]),
        )
        self._write(key, _normalize_value(args[2]), ttl_seconds=int(args[3]))
        for counter in counters:
            self._expire(counter, int(args[4]))
        return 1

    def _eval_cache_delete(self, counters: list[str], *args: Any) -> int:
        key = self._cache_value_key(
            counters,
            _normalize_value(args[0]),
            _normalize_value(args[1]),
        )
        return self._drop(key)

    def _eval_cache_invalidate(self, counters: list[str], *args: Any) -> list[int]:
        versions = []
        for counter in counters:
            version = int(self._read(counter) or "0") + 1
            self._write(counter, str(version))
            self._expire(counter, int(args[0]))
            versions.append(version)
        return versions

    def _eval_consume_challenge(self, numkeys: int, *keys_and_args: Any) -> str:
        if numkeys != 1:
            raise ValueError("CONSUME_CHALLENGE_SCRIPT expects 1 key.")

        challenge_key = _normalize_key(keys_and_args[0])
        presented_value = _normalize_value(keys_and_args[1])

        self._purge_expired(challenge_key)
        if self._store.get(challenge_key) != presented_value:
            return "INVALID"

        self._store.pop(challenge_key, None)
        self._expires.pop(challenge_key, None)
        return "OK"

    def _eval_rotate_refresh_token(
        self,
        numkeys: int,
        *keys_and_args: Any,
    ) -> str:
        if numkeys != 2:
            raise ValueError("ROTATE_REFRESH_TOKEN_SCRIPT expects 2 keys.")

        refresh_key = _normalize_key(keys_and_args[0])
        used_key = _normalize_key(keys_and_args[1])
        expected_jti = _normalize_value(keys_and_args[2])
        used_ttl_seconds = int(keys_and_args[3])
        grace_seconds = int(keys_and_args[4])

        now = int(self.wall_clock())

        self._purge_expired(used_key)
        used_at = self._store.get(used_key)
        if used_at is not None:
            try:
                used_at_number: int | None = int(used_at)
            except ValueError:
                used_at_number = None
            if (
                used_at_number is not None
                and grace_seconds > 0
                and (now - used_at_number) <= grace_seconds
            ):
                return "GRACE"
            return "REUSED"

        self._purge_expired(refresh_key)
        if self._store.get(refresh_key) != expected_jti:
            return "INVALID"

        self._store[used_key] = str(now)
        self._expires[used_key] = _now() + used_ttl_seconds
        self._store.pop(refresh_key, None)
        self._expires.pop(refresh_key, None)
        return "OK"

    async def time(self) -> tuple[int, int]:
        now = self.wall_clock()
        return int(now), int((now % 1) * 1_000_000)

    async def ping(self) -> bool:
        return True

    def keys_snapshot(self) -> list[str]:
        """All keys currently held, string and sorted-set alike.

        For tests asserting a value never leaked into a key, not a Redis API.
        """
        return list({*self._store.keys(), *self._zsets.keys()})

    async def aclose(self) -> None:
        self.closed = True
