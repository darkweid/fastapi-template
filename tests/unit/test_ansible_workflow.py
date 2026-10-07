from pathlib import Path
import re

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ansible.yml"
HOSTED_UBUNTU = re.compile(r"ubuntu-\d\d\.04")


def _runner_labels(job: dict) -> list[str]:
    runs_on = job["runs-on"]
    if runs_on == "${{ matrix.os }}":
        return list(job["strategy"]["matrix"]["os"])
    return list(runs_on) if isinstance(runs_on, list) else [runs_on]


def test_the_ansible_workflow_runs_only_on_github_hosted_ubuntu_runners() -> None:
    """The converge job hardens the machine it runs on. On the organization's
    shared self-hosted runners it would lock out root, replace Docker and enable
    ufw under every other job."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    labels = [label for job in jobs.values() for label in _runner_labels(job)]

    assert labels
    for label in labels:
        assert HOSTED_UBUNTU.fullmatch(label), label
