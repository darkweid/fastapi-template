import os

from tests.contract.redis.backends import REAL_INSTANCE_FLAG


def nested_pytest_env(**overrides: str) -> dict[str, str]:
    """The environment for a pytest run started from inside a test.

    What belongs to the outer run is dropped: PYTEST_ADDOPTS (a replayed seed,
    `-k` or `-x` would change what the nested run collects or reject its
    arguments), the PYTEST_XDIST_* variables of a worker, and REDIS_TEST_INSTANCE,
    which vouches for the outer run's Redis only. `TESTING=true` and `overrides`
    are applied on top.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYTEST_ADDOPTS", REAL_INSTANCE_FLAG}
        and not name.startswith("PYTEST_XDIST_")
    }
    return {**env, "TESTING": "true", **overrides}
