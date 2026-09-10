import pytest

from src.core.errors.exceptions import InfrastructureException
from src.core.redis.core import create_redis_client


def test_a_client_decodes_responses_by_default() -> None:
    client = create_redis_client("redis://localhost:6379/0")

    assert client.connection_pool.connection_kwargs["decode_responses"] is True


def test_a_client_that_would_return_bytes_is_refused() -> None:
    """The rotation script's verdict fails open when it arrives as bytes.

    A bytes verdict matches none of GRACE, REUSED or INVALID, so
    execute_token_rotation reads it as success and admits a reused refresh
    token. The client that would produce it must not be constructible.
    """
    with pytest.raises(InfrastructureException):
        create_redis_client("redis://localhost:6379/0", decode_responses=False)
