import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ops import count_code_lines
from scripts.ops.count_code_lines import (
    Counts,
    count_by_directory,
    count_lines,
    is_plain_relative,
    label,
    main,
    read_paths,
    read_regular_file,
    render,
    top_directory,
    use_color,
)

SCRIPT = Path(count_code_lines.__file__)


def lowest_free_descriptor() -> int:
    """POSIX hands out the lowest free number, so a leak moves it up."""
    descriptor = os.open(os.devnull, os.O_RDONLY)
    os.close(descriptor)
    return descriptor


def test_lines_split_into_code_comments_and_blank() -> None:
    """A docstring is code here: only a line that starts with `#` reads as a
    comment, so the split stays cheap and predictable."""
    counts = count_lines(b'import os\n\n    # note\n"""doc"""\n   \n')
    assert (counts.files, counts.code, counts.comments, counts.blank) == (1, 2, 1, 2)


def test_line_endings_follow_python_not_wc() -> None:
    """Python reads a lone `\\r` as a line break and a form feed as blank
    space, so the count matches what the interpreter sees, not `wc -l`."""
    counts = count_lines(b"a = 1\r\nb = 2\rc = 3\n\x0c\n")
    assert (counts.code, counts.blank) == (3, 1)


def test_a_byte_order_mark_does_not_hide_a_comment() -> None:
    """The mark sits in front of the first line's `#` and would count the
    encoding declaration as code."""
    assert count_lines(b"\xef\xbb\xbf# coding: utf-8\nx = 1\n").comments == 1


def test_an_empty_file_counts_as_a_file() -> None:
    counts = count_lines(b"")
    assert (counts.files, counts.total) == (1, 0)


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


def test_a_directory_in_place_of_a_file_is_left_out_without_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tracked file turned into a directory in the worktree (or a submodule
    whose name matches the pathspec) opens fine; `fdopen` on it raised and
    leaked the descriptor."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg.py").mkdir()
    before = lowest_free_descriptor()
    assert read_regular_file(b"pkg.py") is None
    assert lowest_free_descriptor() == before


def test_an_unreadable_file_stops_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that exists but cannot be read would shrink the total while the
    command still reported success."""
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "secret.py").write_text("x = 1\n")
    (tmp_path / "secret.py").chmod(0)
    with pytest.raises(PermissionError):
        read_regular_file(b"secret.py")


def test_a_file_name_with_a_newline_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paths stay NUL-split bytes end to end, so no character in a name can
    cut it short."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "odd\nname.py").write_text("x = 1\n")
    assert count_by_directory([b"pkg/odd\nname.py"]).by_directory[b"pkg"].files == 1


def test_left_out_paths_are_reported_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent skip makes a wrong total look right."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    tally = count_by_directory([b"a.py", b"gone.py"])
    assert tally.by_directory[b"."].files == 1
    assert tally.left_out == [b"gone.py"]


def test_a_root_level_file_belongs_to_the_dot_row() -> None:
    assert top_directory(b"setup.py") == b"."
    assert top_directory(b"src/app.py") == b"src"


@pytest.mark.parametrize(
    "path", [b"/etc/passwd", b"../outside.py", b"pkg/../x.py", b"./x.py", b"pkg//x.py"]
)
def test_a_path_that_could_leave_the_working_directory_is_refused(
    path: bytes,
) -> None:
    """`git ls-files` run from a subdirectory prints `../` paths; a `..` is a
    real directory, so `O_NOFOLLOW` alone would walk out of the tree."""
    assert not is_plain_relative(path)


def test_a_plain_relative_path_is_accepted() -> None:
    assert is_plain_relative(b"src/app.py")
    assert is_plain_relative(b"setup.py")


def test_a_dot_dot_component_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    assert read_regular_file(b"../outside.py") is None


def test_rows_are_ordered_by_code_and_end_with_the_total() -> None:
    lines = render(
        {b"small": Counts(1, 1, 0, 0), b"big": Counts(2, 10, 1, 1)}, color=False
    )
    assert [line.split()[0] for line in lines] == ["directory", "big", "small", "total"]
    assert lines[-1].split()[1:] == ["3", "11", "1", "1", "13"]


def test_plain_output_carries_no_escape_codes() -> None:
    """A pipe or a file must get text, not terminal control sequences."""
    assert "\033" not in "".join(render({b"src": Counts(1, 1, 0, 0)}, color=False))


def test_colored_output_paints_every_row() -> None:
    lines = render({b"src": Counts(1, 1, 0, 0)}, color=True)
    assert all("\033[" in line for line in lines)


def test_an_empty_listing_renders_a_zero_total() -> None:
    lines = render({}, color=False)
    assert lines[-1].split() == ["total", "0", "0", "0", "0", "0"]


def test_a_long_label_keeps_the_columns_aligned() -> None:
    """A fixed sixteen-character column would push one row's numbers right."""
    lines = render({b"a" * 30: Counts(1, 1, 0, 0), b"b": Counts(1, 1, 0, 0)}, False)
    assert len({len(line) for line in lines}) == 1


def test_paths_split_on_nul_and_repeat_once() -> None:
    """A conflicted path is listed once per stage and must count once."""
    assert read_paths(b"b.py\0a.py\0a.py\0") == [b"a.py", b"b.py"]
    assert read_paths(b"") == []


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


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        (b"odd\ndir", "odd\\ndir"),
        (b"\x1b[31mred", "\\x1b[31mred"),
        (b"caf\xe9", "caf\\xe9"),
        ("zero​width".encode(), "zero\\u200bwidth"),
        ("кириллица".encode(), "кириллица"),
    ],
)
def test_a_name_is_shown_escaped_where_it_is_not_printable(
    raw: bytes, shown: str
) -> None:
    """A raw newline would split one row over two lines, an escape would
    repaint the terminal, and a byte that is not UTF-8 is a lone surrogate
    that `print` cannot encode."""
    assert label(raw) == shown


def test_names_that_escape_alike_stay_separate_rows() -> None:
    """Grouping by the escaped label would merge a directory holding a newline
    with one literally named with a backslash and an `n`."""
    lines = render(
        {b"odd\ndir": Counts(1, 2, 0, 0), b"odd\\ndir": Counts(1, 1, 0, 0)},
        color=False,
    )
    assert len(lines) == 4


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("stream", "environ", "expected"),
    [
        (Terminal(), {}, True),
        (Terminal(), {"NO_COLOR": ""}, True),
        (Terminal(), {"NO_COLOR": "1"}, False),
        (io.StringIO(), {}, False),
        (None, {}, False),
    ],
)
def test_color_needs_a_terminal_and_no_no_color(
    stream: io.StringIO | None, environ: dict[str, str], expected: bool
) -> None:
    """`NO_COLOR` counts only when non-empty, by the convention it follows;
    a closed stdout is `None`, not a terminal."""
    assert use_color(stream, environ) is expected


@pytest.mark.parametrize("stdin", [None, Terminal()])
def test_a_terminal_on_stdin_gets_usage_instead_of_a_wait(
    stdin: io.StringIO | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run by hand without a pipe, the script would sit on `read()` until
    Ctrl-D; a closed stdin is `None` and would raise."""
    monkeypatch.setattr(sys, "stdin", stdin)
    assert main() == 2
    assert "usage" in capsys.readouterr().err


def test_the_command_counts_and_reports_in_one_run(tmp_path: Path) -> None:
    """End to end through a pipe: the table on stdout, the skipped path on
    stderr, no terminal codes in either."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("# head\nx = 1\n\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        input=b"pkg/a.py\0gone.py\0",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    row = result.stdout.decode().splitlines()[1].split()
    assert row == ["pkg", "1", "1", "1", "1", "3"]
    assert result.stderr.decode() == "left out, not a regular file here: gone.py\n"
    assert b"\033" not in result.stdout


def test_a_reader_that_stops_early_leaves_no_traceback(tmp_path: Path) -> None:
    """`| head` closes the pipe; without handling it the interpreter's exit
    flush prints `BrokenPipeError` and exits 120."""
    (tmp_path / "a.py").write_text("x = 1\n")
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr
    process.stdout.close()
    process.stdin.write(b"a.py\0")
    process.stdin.close()
    stderr = process.stderr.read()
    assert process.wait() == 1
    assert stderr == b""
