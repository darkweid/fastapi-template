"""Refuse a migration graph with more than one head.

Alembic answers the same question, but only with the project installed and an
`.env` in place, which is why CI needed a whole dependency install for it. The
graph lives in the files themselves, so reading them is enough - and cheap
enough to run before every commit, which is where a second head is actually
born: merging two branches that each added a migration.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

VERSIONS_PATH = Path("migrations/versions")
REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*(.+)$", re.MULTILINE)
DOWN_REVISION_RE = re.compile(r"^down_revision(?::[^=]+)?\s*=\s*(.+)$", re.MULTILINE)
IDENTIFIER_RE = re.compile(r"""['"]([^'"]+)['"]""")


def parse_revision(source: str) -> tuple[str | None, tuple[str, ...]]:
    """The revision a migration declares and the revisions it sits on top of.

    `down_revision` is a tuple in a merge revision, which is exactly the shape
    that resolves two heads back into one, so it must not be read as a single
    identifier.
    """
    revision_match = REVISION_RE.search(source)
    revision = None
    if revision_match:
        identifiers = IDENTIFIER_RE.findall(revision_match.group(1))
        revision = identifiers[0] if identifiers else None

    down_match = DOWN_REVISION_RE.search(source)
    parents: tuple[str, ...] = ()
    if down_match:
        parents = tuple(IDENTIFIER_RE.findall(down_match.group(1)))

    return revision, parents


def find_heads(versions_path: Path) -> list[str]:
    """The revisions nothing else is built on, sorted for a stable message."""
    revisions: set[str] = set()
    parents: set[str] = set()

    for path in sorted(versions_path.glob("*.py")):
        if path.name == "__init__.py":
            continue

        revision, revision_parents = parse_revision(path.read_text(encoding="utf-8"))
        if revision is None:
            continue

        revisions.add(revision)
        parents.update(revision_parents)

    return sorted(revisions - parents)


def main() -> int:
    if not VERSIONS_PATH.is_dir():
        print(f"Missing directory: {VERSIONS_PATH}", file=sys.stderr)
        return 1

    heads = find_heads(VERSIONS_PATH)
    if len(heads) > 1:
        print(
            "Multiple Alembic heads: "
            + ", ".join(heads)
            + "\nMerge them with `alembic merge` before committing.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
