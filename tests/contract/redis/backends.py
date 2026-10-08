import os

import pytest

FAKE = "fake"
REAL = "real"
REAL_INSTANCE_FLAG = "REDIS_TEST_INSTANCE"

# The marker rides on the param, not on a module: a file of contract scenarios
# can never forget it, and the fake half of the same test stays in `make test`.
REDIS_BACKENDS = [
    pytest.param(FAKE, id=FAKE),
    pytest.param(REAL, id=REAL, marks=pytest.mark.integration),
]


def require_throwaway_instance(target: str) -> None:
    """Fail unless the run names `target` (`host:port`) as a disposable Redis.

    Only `make test-integration` and the CI job set the flag, each to the Redis
    it started itself. Naming the address, not just `1`, keeps a flag left
    exported in a shell from vouching for whatever Redis the settings reach
    later. Fail rather than skip: a skipped real half looks green.
    """
    declared = os.environ.get(REAL_INSTANCE_FLAG)
    if declared != target:
        pytest.fail(
            f"{REAL_INSTANCE_FLAG}={declared!r} does not name the Redis this run "
            f"reaches ({target}), and the real-Redis fixture flushes it. Run it "
            "through `make test-integration`.",
            pytrace=False,
        )
