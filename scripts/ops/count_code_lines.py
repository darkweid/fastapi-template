"""Count lines in the Python files git tracks, per top-level directory.

Reads `git ls-files -z` output on stdin.

Only regular files are read, and only as they are in the worktree: a path git
tracks but the worktree lacks, or holds as a symlink, FIFO or anything else
that is not a regular file, is left out rather than followed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
import stat
import sys

HEADER = ("directory", "files", "code", "comments", "blank", "total")
ROW_FORMAT = "{:<16} {:>7} {:>9} {:>9} {:>9} {:>9}"
BOLD = "1"
DIM = "2"
GREEN = "32"
CYAN = "36"


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


def read_paths(listing: bytes) -> list[bytes]:
    """The listing is `git ls-files -z` output: paths stay bytes split on NUL,
    so no character in a file name is ever re-parsed."""
    return sorted({path for path in listing.split(b"\0") if path})


def open_without_links(path: bytes) -> int:
    """Walks the path one component at a time from the working directory, so a
    symlink is refused wherever it sits, not only in the last component as a
    bare `O_NOFOLLOW` would."""
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
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
    finally:
        os.close(directory)


def read_regular_file(path: bytes) -> bytes | None:
    """`O_NONBLOCK` keeps a FIFO from blocking the open, and the type is checked
    on the descriptor itself, so nothing can be swapped in between the check
    and the read."""
    try:
        descriptor = open_without_links(path)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        return file.read()


def count_lines(content: bytes) -> Counts:
    counts = Counts(files=1)
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            counts.blank += 1
        elif stripped.startswith(b"#"):
            counts.comments += 1
        else:
            counts.code += 1
    return counts


def top_directory(path: bytes) -> str:
    """A name may hold a newline or a terminal escape; those are shown escaped
    so they cannot break the table or reach the terminal."""
    head, separator, _ = path.partition(b"/")
    if not separator:
        return "."
    return "".join(
        char if char.isprintable() else repr(char)[1:-1] for char in os.fsdecode(head)
    )


def count_by_directory(paths: Iterable[bytes]) -> dict[str, Counts]:
    by_directory: dict[str, Counts] = {}
    for path in paths:
        content = read_regular_file(path)
        if content is None:
            continue
        by_directory.setdefault(top_directory(path), Counts()).add(count_lines(content))
    return by_directory


def paint(style: str, text: str, color: bool) -> str:
    return f"\033[{style}m{text}\033[0m" if color else text


def format_row(name: str, counts: Counts, name_style: str, color: bool) -> str:
    return (
        paint(name_style, f"{name:<16}", color)
        + f" {counts.files:>7} "
        + paint(GREEN, f"{counts.code:>9}", color)
        + paint(DIM, f" {counts.comments:>9} {counts.blank:>9}", color)
        + paint(BOLD, f" {counts.total:>9}", color)
    )


def render(by_directory: dict[str, Counts], color: bool) -> list[str]:
    total = Counts()
    lines = [paint(BOLD, ROW_FORMAT.format(*HEADER), color)]
    for name, counts in sorted(
        by_directory.items(), key=lambda item: (-item[1].code, item[0])
    ):
        lines.append(format_row(name, counts, CYAN, color))
        total.add(counts)
    lines.append(format_row("total", total, BOLD, color))
    return lines


def main() -> int:
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    for line in render(count_by_directory(read_paths(sys.stdin.buffer.read())), color):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
