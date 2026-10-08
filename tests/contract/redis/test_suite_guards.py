import os
from pathlib import Path
import subprocess
import sys

import pytest
import redis

from tests.contract.redis.backends import REAL_INSTANCE_FLAG, require_throwaway_instance

CONTRACT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONTRACT_DIR.parents[2]


def _collected_ids(marker_expression: str) -> list[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-m",
            marker_expression,
            str(CONTRACT_DIR),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "TESTING": "true"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_default_run_collects_no_real_redis_test() -> None:
    """`make test` and CI's unit job run without Docker; one real-Redis test
    collected there turns every unit run red on a machine with no Redis."""
    collected = _collected_ids("not integration")

    real_side = [
        test_id
        for test_id in collected
        if "[fake]" not in test_id and "test_suite_guards.py" not in test_id
    ]
    assert any("[fake]" in test_id for test_id in collected)
    assert real_side == []


def test_integration_run_collects_no_fake_test() -> None:
    """The integration run is the only place the real half runs; a fake param
    leaking into it would be harmless but would mean the marker is attached by
    something other than the backend param."""
    collected = _collected_ids("integration")

    assert any("[real]" in test_id for test_id in collected)
    assert [test_id for test_id in collected if "[fake]" in test_id] == []


def test_real_redis_refuses_without_the_throwaway_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real fixture FLUSHALLs; aimed by a stray `pytest -m integration` at a
    developer's own Redis on the default port it would wipe their data."""
    monkeypatch.delenv(REAL_INSTANCE_FLAG, raising=False)

    with pytest.raises(pytest.fail.Exception, match=REAL_INSTANCE_FLAG):
        require_throwaway_instance()


def test_contract_package_does_not_shadow_the_redis_library() -> None:
    """The package is named `redis`; without its `__init__.py` chain pytest would
    put `tests/contract` on sys.path and `import redis` would load the tests."""
    assert redis.__file__ is not None
    assert Path(redis.__file__).resolve().parent.parent.name == "site-packages"
