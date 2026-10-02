import os
from pathlib import Path

import pytest

from scripts.ops.count_code_lines import (
    Counts,
    count_by_directory,
    count_lines,
    label,
    read_paths,
    read_regular_file,
    render,
    top_directory,
)


def test_lines_split_into_code_comments_and_blank() -> None:
    """A docstring is code here: only a line that starts with `#` reads as a
    comment, so the split stays cheap and predictable."""
    counts = count_lines(b'import os\n\n    # note\n"""doc"""\n   \n')
    assert (counts.files, counts.code, counts.comments, counts.blank) == (1, 2, 1, 2)


def test_a_missing_tracked_file_is_left_out(tmp_path: Path) -> None:
    """An unstaged deletion keeps the path in the index; counting it as an
    empty file would inflate the file count."""
    assert read_regular_file(os.fsencode(tmp_path / "gone.py")) is None


def test_a_symlink_is_not_followed(tmp_path: Path) -> None:
    """Following a link counts its target a second time."""
    target = tmp_path / "target.py"
    target.write_text("x = 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(target)
    assert read_regular_file(os.fsencode(link)) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_a_fifo_is_skipped_without_blocking(tmp_path: Path) -> None:
    """Reading a FIFO with no writer would hang the command."""
    fifo = tmp_path / "pipe.py"
    os.mkfifo(fifo)
    assert read_regular_file(os.fsencode(fifo)) is None


def test_a_file_name_with_a_newline_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paths stay NUL-split bytes end to end, so no character in a name can
    cut it short."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "odd\nname.py").write_text("x = 1\n")
    assert count_by_directory([b"pkg/odd\nname.py"])[b"pkg"].files == 1


def test_a_root_level_file_belongs_to_the_dot_row() -> None:
    assert top_directory(b"setup.py") == b"."
    assert top_directory(b"src/app.py") == b"src"


def test_rows_are_ordered_by_code_and_end_with_the_total() -> None:
    lines = render(
        {"small": Counts(1, 1, 0, 0), "big": Counts(2, 10, 1, 1)}, color=False
    )
    assert [line.split()[0] for line in lines] == ["directory", "big", "small", "total"]
    assert lines[-1].split()[1:] == ["3", "11", "1", "1", "13"]


def test_plain_output_carries_no_escape_codes() -> None:
    """A pipe or a file must get text, not terminal control sequences."""
    assert "\033" not in "".join(render({b"src": Counts(1, 1, 0, 0)}, color=False))


def test_paths_split_on_nul_and_repeat_once() -> None:
    """A conflicted path is listed once per stage and must count once."""
    assert read_paths(b"b.py\0a.py\0a.py\0") == [b"a.py", b"b.py"]


def test_a_symlinked_parent_directory_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`O_NOFOLLOW` alone guards only the last component: a tracked directory
    replaced by a link would count files from outside the repository."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.py").write_text("x = 1\n")
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pkg").symlink_to(outside)
    monkeypatch.chdir(repository)
    assert read_regular_file(b"pkg/a.py") is None


def test_a_control_character_in_a_directory_name_is_escaped() -> None:
    """A raw newline would split one row over two lines."""
    assert label(b"odd\ndir") == "odd\\ndir"


def test_names_that_escape_alike_stay_separate_rows() -> None:
    """Grouping by the escaped label would merge a directory holding a newline
    with one literally named with a backslash and an `n`."""
    lines = render(
        {b"odd\ndir": Counts(1, 2, 0, 0), b"odd\\ndir": Counts(1, 1, 0, 0)},
        color=False,
    )
    assert len(lines) == 4
