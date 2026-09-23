from pathlib import Path

from scripts.ops.check_migration_heads import find_heads, parse_revision

MIGRATION = '''"""{summary}"""

revision: str = "{revision}"
down_revision: str | None = {down_revision}
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
'''


def _write(directory: Path, name: str, revision: str, down_revision: str) -> None:
    (directory / name).write_text(
        MIGRATION.format(summary=name, revision=revision, down_revision=down_revision),
        encoding="utf-8",
    )


def test_a_linear_history_has_one_head(tmp_path: Path) -> None:
    _write(tmp_path, "0001_first.py", "aaa", "None")
    _write(tmp_path, "0002_second.py", "bbb", '"aaa"')

    assert find_heads(tmp_path) == ["bbb"]


def test_two_migrations_on_one_parent_are_two_heads(tmp_path: Path) -> None:
    """The shape a branch merge produces, and the one the check exists for."""
    _write(tmp_path, "0001_first.py", "aaa", "None")
    _write(tmp_path, "0002_left.py", "bbb", '"aaa"')
    _write(tmp_path, "0003_right.py", "ccc", '"aaa"')

    assert find_heads(tmp_path) == ["bbb", "ccc"]


def test_a_merge_revision_resolves_both_heads(tmp_path: Path) -> None:
    """`down_revision` is a tuple here; reading only its first identifier would
    leave the other branch looking like a head forever."""
    _write(tmp_path, "0001_first.py", "aaa", "None")
    _write(tmp_path, "0002_left.py", "bbb", '"aaa"')
    _write(tmp_path, "0003_right.py", "ccc", '"aaa"')
    _write(tmp_path, "0004_merge.py", "ddd", '("bbb", "ccc")')

    assert find_heads(tmp_path) == ["ddd"]


def test_the_package_marker_is_not_a_revision(tmp_path: Path) -> None:
    _write(tmp_path, "0001_first.py", "aaa", "None")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")

    assert find_heads(tmp_path) == ["aaa"]


def test_a_tuple_down_revision_is_read_in_full() -> None:
    revision, parents = parse_revision(
        'revision: str = "ddd"\ndown_revision = ("bbb", "ccc")\n'
    )

    assert revision == "ddd"
    assert parents == ("bbb", "ccc")


def test_the_projects_own_migrations_have_one_head() -> None:
    """Pins the repository itself, so a merge that forks the graph fails here
    even when nobody runs the hook."""
    assert len(find_heads(Path("migrations/versions"))) == 1
