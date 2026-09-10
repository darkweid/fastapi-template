from redis.asyncio import Redis


def create_redis_client(
    connection_url: str,
    *,
    socket_timeout: float = 5.0,
    socket_connect_timeout: float = 5.0,
    health_check_interval: int = 30,
) -> Redis:
    """Create a Redis async client from URL. Connects lazily, on first command.

    Decoding is not a parameter: the auth layer compares Lua verdicts and
    stored jti values against string literals, and every one of those
    comparisons is wrong against bytes. No caller may ask for the other one.
    """
    return Redis.from_url(
        connection_url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        health_check_interval=health_check_interval,
    )
