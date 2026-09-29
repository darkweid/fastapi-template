import hashlib
import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_UINT64_MAX = 2**64
_INT64_MAX = 2**63
# statement_timeout is an int GUC in milliseconds; PostgreSQL refuses anything above.
_STATEMENT_TIMEOUT_MAX_MS = 2**31 - 1


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


async def set_local_statement_timeout(session: AsyncSession, seconds: float) -> None:
    """
    Cancel any statement that runs longer than `seconds` until the transaction ends.

    The setting is transaction-local (`set_config(..., is_local => true)`, the function
    form of `SET LOCAL`, which takes no bind parameters): COMMIT or ROLLBACK restores the
    previous value, so it never outlives the transaction on a pooled connection. Rolling
    back a savepoint opened before the call restores it too. A cancelled statement fails
    with SQLSTATE 57014 and aborts the transaction.

    `seconds` is rounded up to whole milliseconds and must be positive: PostgreSQL reads
    0 as "no limit", which is the opposite of what a caller asking for a timeout means.

    Raises ValueError for a non-positive, non-finite or too large value, and RuntimeError
    outside a transaction: the session would autobegin one of its own, and the limit
    would end whenever that implicit transaction does.
    """
    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"statement timeout must be a positive number of seconds, got {seconds!r}"
        )
    # Compared before `ceil`: a huge finite float multiplies to inf, and `ceil(inf)`
    # raises OverflowError instead of the ValueError callers handle.
    exact_milliseconds = seconds * 1000
    if exact_milliseconds > _STATEMENT_TIMEOUT_MAX_MS:
        raise ValueError(
            f"statement timeout must not exceed {_STATEMENT_TIMEOUT_MAX_MS} ms, got {seconds!r} s"
        )
    milliseconds = math.ceil(exact_milliseconds)
    if not session.in_transaction():
        raise RuntimeError("set_local_statement_timeout requires an active transaction")
    await session.execute(
        select(func.set_config("statement_timeout", str(milliseconds), True))
    )
