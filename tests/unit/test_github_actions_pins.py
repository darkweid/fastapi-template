from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.MULTILINE)
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
RELEASE_COMMENT = re.compile(r"^\s+# v\d+\.\d+\.\d+$")


def test_every_third_party_action_is_pinned_to_a_commit() -> None:
    """A tag can be moved to other code by whoever controls the action's
    repository, and the next run executes it with this repository's token;
    a commit SHA cannot move. The comment keeps the release readable."""
    files = [
        *(PROJECT_ROOT / ".github").rglob("*.yml"),
        *(PROJECT_ROOT / ".github").rglob("*.yaml"),
    ]
    found = 0
    for path in files:
        for reference, rest in USES.findall(path.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            found += 1
            assert PINNED.match(reference), f"{path.name}: {reference}"
            assert RELEASE_COMMENT.match(rest), f"{path.name}: {reference}{rest}"

    assert found
