"""Deny by default: a path is documentation only when a rule below names it, so
an unfamiliar path, a new directory or a workflow edit all read as code. A wrong
`true` here is invisible - GitHub counts a job skipped through `if:` as a passing
required check, so untested code would merge under a green PR - which is why
anything not clearly documentation answers `false`."""

from collections.abc import Iterable
import sys

DOCUMENTATION_DIRECTORY = "docs/"
DOCUMENTATION_FILES = frozenset({"LICENSE"})


def is_documentation(path: str) -> bool:
    return (
        path.endswith(".md")
        or path.startswith(DOCUMENTATION_DIRECTORY)
        or path in DOCUMENTATION_FILES
    )


def changes_are_documentation_only(paths: Iterable[str]) -> bool:
    """An empty change set answers False: nothing was compared, so nothing
    licenses a skip."""
    changed = list(paths)
    return bool(changed) and all(is_documentation(path) for path in changed)


def main() -> int:
    changed = [line.strip() for line in sys.stdin if line.strip()]
    print("true" if changes_are_documentation_only(changed) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
