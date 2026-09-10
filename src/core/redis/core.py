from redis.asyncio import Redis

from src.core.errors.exceptions import InfrastructureException


def create_redis_client(
    connection_url: str,
    *,
    decode_responses: bool = True,
    socket_timeout: float = 5.0,
    socket_connect_timeout: float = 5.0,
    health_check_interval: int = 30,
) -> Redis:
    """Create a Redis async client from URL. Connects lazily, on first command.

    `decode_responses` must stay true: the auth layer compares Lua verdicts
    against string literals, and the rotation one fails open if they arrive as
    bytes - a verdict matching none of GRACE, REUSED or INVALID is read as
    success, which admits a reused refresh token.
    """
    if not decode_responses:
        raise InfrastructureException(
            "Redis clients must decode responses: the auth layer compares "
            "Lua verdicts and stored tokens as strings."
        )

    return Redis.from_url(
        connection_url,
        decode_responses=decode_responses,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        health_check_interval=health_check_interval,
    )
