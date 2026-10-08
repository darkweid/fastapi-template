import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import redis

from tests.contract.redis.backends import FAKE, REAL, REAL_INSTANCE_FLAG

CONTRACT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONTRACT_DIR.parents[2]
REAL_ITEM = (
    "tests/contract/redis/test_cache_contract.py::test_an_unknown_key_misses[real]"
)


def _nested_pytest(
    *arguments: str, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # PYTEST_ADDOPTS belongs to the outer run: a replayed seed, `-k` or `-x` there
    # would change what the nested run collects or reject its arguments outright.
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYTEST_ADDOPTS", REAL_INSTANCE_FLAG}
    }
    env.update({"TESTING": "true", **(env_overrides or {})})
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_ids(marker_expression: str) -> list[str]:
    result = _nested_pytest(
        "--collect-only", "-q", "-m", marker_expression, str(CONTRACT_DIR)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def _backend_of(test_id: str) -> str | None:
    """The backend param among a test id's params, whatever else it is
    parametrized by and in whichever order pytest joined the ids."""
    if not test_id.endswith("]"):
        return None
    param_ids = set(test_id.rsplit("[", 1)[1].rstrip("]").split("-"))
    backends = param_ids & {FAKE, REAL}
    return backends.pop() if backends else None


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_default_run_collects_no_real_redis_test() -> None:
    """`make test` and CI's unit job run without Docker; one real-Redis test
    collected there turns every unit run red on a machine with no Redis."""
    collected = _collected_ids("not integration")

    real_side = [
        test_id
        for test_id in collected
        if _backend_of(test_id) != FAKE and "test_suite_guards.py" not in test_id
    ]
    assert any(_backend_of(test_id) == FAKE for test_id in collected)
    assert real_side == []


def test_integration_run_collects_no_fake_test() -> None:
    """The integration run is the only place the real half runs; a fake param
    leaking into it would be harmless but would mean the marker is attached by
    something other than the backend param."""
    collected = _collected_ids("integration")

    assert any(_backend_of(test_id) == REAL for test_id in collected)
    assert [test_id for test_id in collected if _backend_of(test_id) == FAKE] == []


@pytest.mark.parametrize(
    "declared_target",
    [None, "127.0.0.1:6379"],
    ids=["flag_unset", "flag_names_another_redis"],
)
def test_real_redis_refuses_before_reaching_the_instance(
    declared_target: str | None,
) -> None:
    """The real fixture FLUSHALLs; aimed by a stray `pytest -m integration` at a
    developer's own Redis it would wipe their data. The refusal has to come before
    the first command: pointed at a port nothing listens on, a fixture that
    connected first would fail with a ConnectionError instead."""
    unused_port = _unused_port()
    overrides = {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(unused_port)}
    if declared_target is not None:
        overrides[REAL_INSTANCE_FLAG] = declared_target

    result = _nested_pytest("-m", "integration", REAL_ITEM, env_overrides=overrides)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert REAL_INSTANCE_FLAG in output
    assert f"127.0.0.1:{unused_port}" in output
    assert "ConnectionError" not in output


def test_contract_package_does_not_shadow_the_redis_library() -> None:
    """The package is named `redis`; without its `__init__.py` chain pytest would
    put `tests/contract` on sys.path and `import redis` would load the tests."""
    assert redis.__file__ is not None
    assert not Path(redis.__file__).resolve().is_relative_to(REPO_ROOT / "tests")
