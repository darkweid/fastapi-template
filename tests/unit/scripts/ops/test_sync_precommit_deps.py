from pathlib import Path

import pytest

from scripts.ops.sync_precommit_deps import (
    parse_requirements_versions,
    sync_hook_rev,
    sync_mypy_additional_dependencies,
)

CONFIG = """repos:
  - repo: https://github.com/psf/black
    rev: 25.9.0
    hooks:
      - id: black

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v2.1.0
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic==2.0.0
          - sqlalchemy==2.0.0

  - repo: https://github.com/myint/autoflake
    rev: v2.3.3
    hooks:
      - id: autoflake
"""


def test_lockfile_pins_with_extras_resolve_to_the_base_package() -> None:
    # pip-compile writes the requested extras into the lockfile line
    # (taskiq[reload]==0.12.4), and the version applies to the base package.
    versions = parse_requirements_versions(
        "taskiq[reload]==0.12.4\nmypy==2.3.0\n# comment\n"
    )

    assert versions == {"taskiq": "0.12.4", "mypy": "2.3.0"}


MYPY_MARKER = "- repo: https://github.com/pre-commit/mirrors-mypy"
BANDIT_MARKER = "- repo: https://github.com/PyCQA/bandit"
LOCKFILE = Path("infra/requirements/dev.txt")

BANDIT_BLOCK = """
  - repo: https://github.com/PyCQA/bandit
    rev: 1.0.0
    hooks:
      - id: bandit
"""


def _sync_mypy(config_text: str, versions: dict[str, str]) -> str:
    return sync_hook_rev(
        config_text,
        versions,
        repo_marker=MYPY_MARKER,
        package="mypy",
        requirements_path=LOCKFILE,
        tag_prefix="v",
    )


def test_rev_is_rewritten_to_the_dev_lockfile_mypy_version() -> None:
    updated = _sync_mypy(CONFIG, {"mypy": "2.3.0"})

    assert "rev: v2.3.0" in updated
    # Repos before and after the mypy block must keep their own revs.
    assert "rev: 25.9.0" in updated
    assert "rev: v2.3.3" in updated


def test_an_empty_tag_prefix_pins_the_bare_version() -> None:
    """bandit tags releases as 1.9.4, the mypy mirror as v2.3.0; a shared
    prefix would pin one of the two to a tag that does not exist."""
    updated = sync_hook_rev(
        CONFIG + BANDIT_BLOCK,
        {"bandit": "1.9.4"},
        repo_marker=BANDIT_MARKER,
        package="bandit",
        requirements_path=LOCKFILE,
        tag_prefix="",
    )

    assert "rev: 1.9.4" in updated
    assert "rev: v1.9.4" not in updated


def test_rev_already_in_sync_leaves_the_config_unchanged() -> None:
    in_sync = CONFIG.replace("rev: v2.1.0", "rev: v2.3.0")

    assert _sync_mypy(in_sync, {"mypy": "2.3.0"}) == in_sync


def test_missing_mypy_pin_in_requirements_raises() -> None:
    with pytest.raises(RuntimeError, match="mypy"):
        _sync_mypy(CONFIG, {"pydantic": "2.0.0"})


def test_missing_rev_line_in_mypy_block_raises() -> None:
    broken = CONFIG.replace("    rev: v2.1.0\n", "")

    with pytest.raises(RuntimeError, match="rev"):
        _sync_mypy(broken, {"mypy": "2.3.0"})


def test_additional_dependencies_are_pinned_from_requirements() -> None:
    updated = sync_mypy_additional_dependencies(
        CONFIG, {"pydantic": "2.13.4", "sqlalchemy": "2.0.52", "mypy": "2.3.0"}
    )

    assert "- pydantic==2.13.4" in updated
    assert "- sqlalchemy==2.0.52" in updated
