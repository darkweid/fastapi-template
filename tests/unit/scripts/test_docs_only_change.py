import io

import pytest

from scripts.docs_only_change import (
    changes_are_documentation_only,
    is_documentation,
    main,
)


def test_a_code_path_alongside_documentation_still_counts_as_code() -> None:
    """One source file has to bring the whole pipeline back: GitHub reports a
    job skipped through `if:` as a passing required check, so a wrong `true`
    merges untested code under a green PR."""
    assert not changes_are_documentation_only(["README.md", "src/note/routers.py"])


def test_an_empty_change_set_counts_as_code() -> None:
    """An unusable base - a branch's first push, a force-push that orphaned the
    previous tip - produces no diff at all, and that is absence of evidence, not
    evidence the change is documentation."""
    assert not changes_are_documentation_only([])


def test_a_workflow_edit_counts_as_code() -> None:
    """The pipeline definition decides what runs at all, so a change to it can
    never be waved through by the filter it configures."""
    assert not changes_are_documentation_only([".github/workflows/_ci.yml"])


def test_the_tracked_documentation_surface_counts_as_documentation() -> None:
    """These are the paths the filter exists for. If any of them stops matching,
    every documentation change starts paying for a full pipeline again."""
    assert changes_are_documentation_only(
        [
            "README.md",
            "docs/readme/bootstrap.md",
            "tests/TEST_GUIDE.md",
            "infra/firewall/README.md",
            "LICENSE",
        ]
    )


def test_a_non_markdown_file_under_docs_counts_as_documentation() -> None:
    """.dockerignore keeps docs/ out of the image and nothing imports it, so the
    whole directory sits outside the build - deliberately, not by accident."""
    assert is_documentation("docs/src/user/auth/example.py")


def test_the_documentation_directory_boundary_is_the_slash() -> None:
    """Dropping the slash from the `docs/` prefix still passes every other test
    here while blessing a sibling directory: `docs-site/app.py` would become
    documentation and its whole pipeline would stop running."""
    assert not is_documentation("docs")
    assert not is_documentation("docs-site/app.py")
    assert not is_documentation("docsrc/conf.py")


def test_the_markdown_suffix_is_case_sensitive() -> None:
    """A rule loosened to catch `README.MD` would also have to decide about
    `.markdown` and `.mdx`; the narrow suffix is what keeps the set enumerable,
    and a path it does not match costs a pipeline run, never a skipped one."""
    assert not is_documentation("README.MD")
    assert not is_documentation("guide.markdown")


def test_the_stdin_contract_is_the_bare_lowercase_literal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The composite action string-compares this output against `true` inside a
    shell. A `print(True)` regression would answer `True`, never equal `true`,
    and every documentation change would quietly go back to a full pipeline."""
    monkeypatch.setattr("sys.stdin", io.StringIO("  README.md  \n\ndocs/a.md\n"))

    assert main() == 0
    assert capsys.readouterr().out == "true\n"


def test_empty_stdin_answers_false(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The action feeds an empty diff through as a single blank line, and the
    answer there has to be the one that runs the pipeline."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))

    assert main() == 0
    assert capsys.readouterr().out == "false\n"
