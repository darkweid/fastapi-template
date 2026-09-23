from __future__ import annotations

import argparse
from pathlib import Path
import re

DIRECTIVE_PREFIXES = (
    "-r",
    "-c",
    "-e",
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--trusted-host",
    "--no-binary",
    "--only-binary",
    "--prefer-binary",
    "--pre",
)
NAME_SPLIT_PATTERN = re.compile(r"[\s\[<>=!~;]")


def is_directive(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in DIRECTIVE_PREFIXES)


def requirement_sort_key(line: str) -> str:
    stripped = line.strip()
    requirement_name = NAME_SPLIT_PATTERN.split(stripped, maxsplit=1)[0]
    return requirement_name.lower().replace("_", "-")


def split_blocks(content: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current_block: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current_block:
                blocks.append(current_block)
                current_block = []
            continue
        current_block.append(line)

    if current_block:
        blocks.append(current_block)

    return blocks


def sort_block(lines: list[str]) -> list[str]:
    directives: list[list[str]] = []
    requirements: list[tuple[str, list[str]]] = []
    pending_comments: list[str] = []
    trailing_comments: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending_comments.append(line)
            continue

        item_lines = [*pending_comments, line]
        pending_comments = []

        if is_directive(stripped):
            directives.append(item_lines)
            continue

        requirements.append((requirement_sort_key(stripped), item_lines))

    if pending_comments:
        trailing_comments = pending_comments

    requirements.sort(key=lambda item: item[0])

    output_lines: list[str] = []
    for directive_lines in directives:
        output_lines.extend(directive_lines)

    if directives and requirements:
        output_lines.append("")

    for _, requirement_lines in requirements:
        output_lines.extend(requirement_lines)

    if trailing_comments:
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        output_lines.extend(trailing_comments)

    return output_lines


def sort_requirements_in_content(content: str) -> str:
    blocks = split_blocks(content)
    sorted_blocks = [sort_block(block) for block in blocks]
    flattened_lines: list[str] = []

    for index, block_lines in enumerate(sorted_blocks):
        if index:
            flattened_lines.append("")
        flattened_lines.extend(block_lines)

    return "\n".join(flattened_lines).rstrip() + "\n"


def sort_requirements_in_file(path: Path) -> bool:
    original_content = path.read_text(encoding="utf-8")
    sorted_content = sort_requirements_in_content(original_content)

    if sorted_content == original_content:
        return False

    path.write_text(sorted_content, encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sort requirement entries inside pip-tools .in files."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    for path in args.paths:
        sort_requirements_in_file(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
