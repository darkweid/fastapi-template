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


def test_the_ansible_workflow_starts_when_the_make_targets_it_runs_change() -> None:
    """The jobs run `make ansible-deps` and friends, and the generic CI never does,
    so a broken recipe in the Makefile would otherwise merge untested."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow[True]
    commands = [
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job["steps"]
    ]

    assert any(re.search(r"\bmake\b", command) for command in commands)
    for event in ("pull_request", "push"):
        assert "Makefile" in triggers[event]["paths"], event
