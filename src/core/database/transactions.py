import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_UINT64_MAX = 2**64
_INT64_MAX = 2**63


def _string_to_int64(key: str) -> int:
    """
    Convert a string key to a signed 64-bit integer using a stable hash.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big", signed=False)
    if value >= _INT64_MAX:
        return value - _UINT64_MAX
    return value


async def advisory_xact_lock(session: AsyncSession, key: str) -> None:
    """
    Acquire a PostgreSQL advisory transaction lock for the given key.

    The lock is held until the surrounding transaction ends.
    """
    if not session.in_transaction():
        raise RuntimeError("advisory_xact_lock requires an active transaction")
    lock_key = _string_to_int64(key)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def try_advisory_xact_lock(session: AsyncSession, key: str) -> bool:
    """
    Try to acquire a PostgreSQL advisory transaction lock for the given key.

    Returns True if the lock was acquired, False otherwise.
    """
    if not session.in_transaction():
        raise RuntimeError("try_advisory_xact_lock requires an active transaction")
    lock_key = _string_to_int64(key)
    result = await session.execute(select(func.pg_try_advisory_xact_lock(lock_key)))
    return bool(result.scalar_one())
