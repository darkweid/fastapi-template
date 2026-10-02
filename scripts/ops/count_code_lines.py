r"""Count lines in the Python files git tracks, per top-level directory.

Reads `git ls-files -z` output on stdin: NUL-separated paths relative to the
working directory, which is the repository root when the Makefile runs it.
Only regular files are read, and only as they are in the worktree: a path git
tracks but the worktree lacks, or holds as a symlink, a directory, a FIFO or
anything else that is not a regular file, is left out and named on stderr
rather than followed. So is a path with an absolute, `.` or `..` component,
because the walk must never leave the working directory. Any other failure to
open or read a file (permissions, descriptor limit, I/O) stops the command
instead of shrinking the total.

The split is a heuristic over bytes, not a parse: a line whose first non-blank
character is `#` is a comment even inside a string literal, a docstring is
code, and a line ends at `\n`, `\r\n` or a lone `\r`, as Python reads source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import errno
import io
import os
import stat
import sys
from typing import TextIO

HEADER = ("directory", "files", "code", "comments", "blank", "total")
NAME_WIDTH = 16
ROW_FORMAT = "{:<{width}} {:>7} {:>9} {:>9} {:>9} {:>9}"
BOM = b"\xef\xbb\xbf"
USAGE = "usage: git ls-files -z -- '*.py' | python3 scripts/ops/count_code_lines.py"
BOLD = "1"
DIM = "2"
GREEN = "32"
CYAN = "36"
# What a path that is absent or not a regular file answers to the walk: a
# missing component, a symlink refused by O_NOFOLLOW (ELOOP on Linux and
# macOS, EMLINK on FreeBSD), a socket or device that cannot be opened.
NOT_A_REGULAR_FILE = frozenset(
    {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.ELOOP,
        errno.EMLINK,
        errno.ENXIO,
        errno.EOPNOTSUPP,
    }
)


@dataclass
class Counts:
    files: int = 0
    code: int = 0
    comments: int = 0
    blank: int = 0

    @property
    def total(self) -> int:
        return self.code + self.comments + self.blank

    def add(self, other: Counts) -> None:
        self.files += other.files
        self.code += other.code
        self.comments += other.comments
        self.blank += other.blank


@dataclass
class Tally:
    by_directory: dict[bytes, Counts] = field(default_factory=dict)
    left_out: list[bytes] = field(default_factory=list)


def read_paths(listing: bytes) -> list[bytes]:
    """The listing is `git ls-files -z` output: paths stay bytes split on NUL,
    so no character in a file name is ever re-parsed. A path listed more than
    once (a conflicted file has a stage per side) is kept once."""
    return sorted({path for path in listing.split(b"\0") if path})


def is_plain_relative(path: bytes) -> bool:
    """False for an absolute path or one with a `.`, `..` or empty component:
    git lists none of those from the repository root, and a `..` would walk
    the open in `open_without_links` out of the working directory."""
    return all(component not in (b"", b".", b"..") for component in path.split(b"/"))


def open_without_links(path: bytes) -> int:
    """Walks the path one component at a time from the working directory, so a
    symlink is refused wherever it sits, not only in the last component as a
    bare `O_NOFOLLOW` would. `O_NONBLOCK` keeps a FIFO from blocking the open
    and `O_NOCTTY` keeps a device node from becoming the controlling terminal.
    Raises `OSError` as the opens do; the caller owns the descriptor."""
    *parents, name = path.split(b"/")
    directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parents:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        return os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
            dir_fd=directory,
        )
    finally:
        os.close(directory)


def read_regular_file(path: bytes) -> bytes | None:
    """`None` for a path that is absent or not a regular file here; any other
    `OSError` propagates. The type is checked on the descriptor itself, so
    nothing can be swapped in between the check and the read, and the
    descriptor is closed on every path out."""
    if not is_plain_relative(path):
        return None
    try:
        descriptor = open_without_links(path)
    except OSError as error:
        if error.errno in NOT_A_REGULAR_FILE:
            return None
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with open(descriptor, "rb", closefd=False) as file:
            return file.read()
    finally:
        os.close(descriptor)


def count_lines(content: bytes) -> Counts:
    counts = Counts(files=1)
    for line in content.removeprefix(BOM).splitlines():
        stripped = line.strip()
        if not stripped:
            counts.blank += 1
        elif stripped.startswith(b"#"):
            counts.comments += 1
        else:
            counts.code += 1
    return counts


def top_directory(path: bytes) -> bytes:
    head, separator, _ = path.partition(b"/")
    return head if separator else b"."


def escape(char: str) -> str:
    if char.isprintable():
        return char
    if "\udc80" <= char <= "\udcff":
        # `os.fsdecode` keeps a byte that is not UTF-8 as a lone surrogate;
        # the byte itself is what the reader can match against `ls`.
        return repr(char.encode("utf-8", "surrogateescape"))[2:-1]
    return repr(char)[1:-1]


def label(directory: bytes) -> str:
    r"""A name may hold a newline, a terminal escape or a byte that is not
    UTF-8; those are shown escaped (`\n`, `\x1b`, `\xe9`) so they cannot break
    the table or reach the terminal. Rows are grouped by the raw name, so two
    names that escape alike still stay apart."""
    return "".join(escape(char) for char in os.fsdecode(directory))


def count_by_directory(paths: Iterable[bytes]) -> Tally:
    tally = Tally()
    for path in paths:
        content = read_regular_file(path)
        if content is None:
            tally.left_out.append(path)
            continue
        counts = tally.by_directory.setdefault(top_directory(path), Counts())
        counts.add(count_lines(content))
    return tally


def use_color(stream: TextIO | None, environ: Mapping[str, str]) -> bool:
    """Colour only on a terminal, and never under `NO_COLOR`, whose convention
    is that any non-empty value turns colour off."""
    return stream is not None and stream.isatty() and not environ.get("NO_COLOR")


def paint(style: str, text: str, color: bool) -> str:
    return f"\033[{style}m{text}\033[0m" if color else text


def format_row(
    name: str, counts: Counts, name_style: str, width: int, color: bool
) -> str:
    return (
        paint(name_style, f"{name:<{width}}", color)
        + f" {counts.files:>7} "
        + paint(GREEN, f"{counts.code:>9}", color)
        + paint(DIM, f" {counts.comments:>9} {counts.blank:>9}", color)
        + paint(BOLD, f" {counts.total:>9}", color)
    )


def render(by_directory: dict[bytes, Counts], color: bool) -> list[str]:
    """Rows are ordered by code lines, then by the raw name, so the order does
    not depend on how a name escapes; the name column widens to the longest
    label so a long or escaped name keeps the numbers aligned."""
    rows = [
        (label(name), counts)
        for name, counts in sorted(
            by_directory.items(), key=lambda item: (-item[1].code, item[0])
        )
    ]
    width = max([NAME_WIDTH, *(len(name) for name, _ in rows)])
    total = Counts()
    lines = [paint(BOLD, ROW_FORMAT.format(*HEADER, width=width), color)]
    for name, counts in rows:
        lines.append(format_row(name, counts, CYAN, width, color))
        total.add(counts)
    lines.append(format_row("total", total, BOLD, width, color))
    return lines


def main() -> int:
    if sys.stdin is None or sys.stdin.isatty():
        print(USAGE, file=sys.stderr)
        return 2
    tally = count_by_directory(read_paths(sys.stdin.buffer.read()))
    for path in tally.left_out:
        print(f"left out, not a regular file here: {label(path)}", file=sys.stderr)
    stdout = sys.stdout
    if stdout is None:
        return 0
    if isinstance(stdout, io.TextIOWrapper):
        # A name the terminal's encoding cannot carry is shown escaped instead
        # of aborting the table on a UnicodeEncodeError.
        stdout.reconfigure(errors="backslashreplace")
    try:
        for line in render(tally.by_directory, use_color(stdout, os.environ)):
            print(line)
        stdout.flush()
    except BrokenPipeError:
        # The reader is gone (`| head`); pointing the descriptor at /dev/null
        # keeps the interpreter's exit flush from reporting the pipe again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), stdout.fileno())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
