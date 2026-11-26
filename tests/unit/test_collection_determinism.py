from pathlib import Path
import subprocess
import sys

from tests.helpers.nested_pytest import nested_pytest_env

REPO_ROOT = Path(__file__).resolve().parents[2]


def _collected_ids(hash_seed: str) -> list[str]:
    # `-m ""` lifts the default marker filter, so the integration suite is checked
    # too. A different PYTHONHASHSEED per collection catches an id or an order
    # that follows set or dict iteration.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=nested_pytest_env(PYTHONHASHSEED=hash_seed),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_two_collections_give_the_same_test_ids() -> None:
    """Every xdist worker collects the suite itself and the run stops when two
    disagree, in the ids or in their order. An id built from `uuid4()` or the
    clock (a parametrize value rendered into the id) disagrees on every run or,
    for a timestamp, on one run in many; pass such a parameter an explicit
    `ids=`."""
    assert _collected_ids("0") == _collected_ids("1")
