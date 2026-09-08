from redis.asyncio import Redis


def create_redis_client(
    connection_url: str,
    *,
    decode_responses: bool = True,
    socket_timeout: float = 5.0,
    socket_connect_timeout: float = 5.0,
    health_check_interval: int = 30,
) -> Redis:
    """Create a Redis async client from URL. Connects lazily, on first command."""
    return Redis.from_url(
        connection_url,
        decode_responses=decode_responses,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        health_check_interval=health_check_interval,
    )
