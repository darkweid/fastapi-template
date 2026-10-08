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


def require_throwaway_instance() -> None:
    """Fail unless the run says the Redis it reaches is disposable.

    Only `make test-integration` and the CI job set the flag, and both start a
    Redis of their own. Fail rather than skip: a skipped real half looks green.
    """
    if os.environ.get(REAL_INSTANCE_FLAG) != "1":
        pytest.fail(
            f"{REAL_INSTANCE_FLAG}=1 is not set: the real-Redis fixture flushes the "
            "instance it reaches. Run it through `make test-integration`.",
            pytrace=False,
        )
