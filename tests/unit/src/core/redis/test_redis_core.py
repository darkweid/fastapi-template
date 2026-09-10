from src.core.redis.core import create_redis_client


def test_a_client_decodes_responses() -> None:
    """Every auth comparison - Lua verdicts, stored jti values - is a string
    comparison, and each one is wrong against bytes."""
    client = create_redis_client("redis://localhost:6379/0")

    assert client.connection_pool.connection_kwargs["decode_responses"] is True
