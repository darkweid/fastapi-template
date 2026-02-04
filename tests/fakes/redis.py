from __future__ import annotations

import fnmatch
import hashlib
import time
from typing import Any

import redis.exceptions as redis_exc

from src.user.auth.redis_scripts import ROTATE_REFRESH_TOKEN_SCRIPT


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


class InMemoryRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expires: dict[str, float] = {}
        self._scripts: dict[str, str] = {}
        self._evalsha_overrides: dict[str, int] = {}
        self.closed = False

    def set_evalsha_result(self, key: str, result: int) -> None:
        self._evalsha_overrides[key] = result

    def clear_evalsha_overrides(self) -> None:
        self._evalsha_overrides.clear()

    def _purge_expired(self, key: str) -> None:
        expires_at = self._expires.get(key)
        if expires_at is None:
            return
        if _now() >= expires_at:
            self._store.pop(key, None)
            self._expires.pop(key, None)

    async def get(self, key: str | bytes) -> str | None:
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
    ) -> bool:
        key_norm = _normalize_key(key)
        self._store[key_norm] = _normalize_value(value)
        if ex is not None:
            self._expires[key_norm] = _now() + int(ex)
        elif px is not None:
            self._expires[key_norm] = _now() + (int(px) / 1000)
        else:
            self._expires.pop(key_norm, None)
        return True

    async def setex(self, key: str | bytes, time_seconds: int, value: Any) -> bool:
        return await self.set(key, value, ex=time_seconds)

    async def delete(self, *keys: str | bytes) -> int:
        deleted = 0
        for key in keys:
            key_norm = _normalize_key(key)
            self._purge_expired(key_norm)
            if key_norm in self._store:
                self._store.pop(key_norm, None)
                self._expires.pop(key_norm, None)
                deleted += 1
        return deleted

    async def exists(self, key: str | bytes) -> int:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        return int(key_norm in self._store)

    async def expire(self, key: str | bytes, seconds: int) -> bool:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        if key_norm not in self._store:
            return False
        self._expires[key_norm] = _now() + int(seconds)
        return True

    async def ttl(self, key: str | bytes) -> int:
        key_norm = _normalize_key(key)
        self._purge_expired(key_norm)
        if key_norm not in self._store:
            return -2
        expires_at = self._expires.get(key_norm)
        if expires_at is None:
            return -1
        return max(0, int(expires_at - _now()))

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
        if key in self._evalsha_overrides:
            return self._evalsha_overrides[key]
        return 0

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> str:
        if script.strip() == ROTATE_REFRESH_TOKEN_SCRIPT.strip():
            return await self._eval_rotate_refresh_token(numkeys, *keys_and_args)
        raise NotImplementedError("Script not supported in fake Redis.")

    async def _eval_rotate_refresh_token(
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

        if await self.exists(used_key):
            return "REUSED"

        stored_jti = await self.get(refresh_key)
        if stored_jti != expected_jti:
            return "INVALID"

        await self.setex(used_key, used_ttl_seconds, "used")
        await self.delete(refresh_key)
        return "OK"

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True
