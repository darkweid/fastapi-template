import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


def _collected_ids() -> list[str]:
    # The outer run's PYTEST_ADDOPTS and xdist variables would change what the
    # nested run collects; `-m ""` lifts the default marker filter, so the
    # integration suite is checked too.
    env = {
        name: value
        for name, value in os.environ.items()
        if name != "PYTEST_ADDOPTS" and not name.startswith("PYTEST_XDIST_")
    }
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
        env={**env, "TESTING": "true"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sorted(line for line in result.stdout.splitlines() if "::" in line)


def test_two_collections_give_the_same_test_ids() -> None:
    """Every xdist worker collects the suite itself and the run stops when two
    disagree. An id built from `uuid4()` or the clock (a parametrize value
    rendered into the id) disagrees on every run or, for a timestamp, on one run
    in many; pass such a parameter an explicit `ids=`."""
    first = _collected_ids()
    second = _collected_ids()

    assert sorted(set(first) ^ set(second)) == []
    assert len(first) == len(second)
