from __future__ import annotations

from pathlib import Path
import re

PRECOMMIT_CONFIG_PATH = Path(".pre-commit-config.yaml")
DEV_REQUIREMENTS_PATH = Path("infra/requirements/dev.txt")
MYPY_REPO_MARKER = "- repo: https://github.com/pre-commit/mirrors-mypy"
ADDITIONAL_DEPS_MARKER = "additional_dependencies:"
REQ_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^#\s]+)")
SPEC_SPLIT_RE = re.compile(r"(==|>=|<=|~=|!=|>|<)")


def normalize_package_name(name: str) -> str:
    return name.lower().replace("_", "-")


def parse_requirements_versions(requirements_text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = REQ_PIN_RE.match(line)
        if not match:
            continue

        package_name, version = match.groups()
        versions[normalize_package_name(package_name)] = version

    return versions


def extract_dep_package(dep_spec: str) -> tuple[str, str]:
    """
    Returns:
    - package token as written in pre-commit config (can include extras)
    - normalized base package name for requirements lookup
    """
    dep_spec = dep_spec.strip()
    if not dep_spec:
        return "", ""

    split_result = SPEC_SPLIT_RE.split(dep_spec, maxsplit=1)
    package_token = split_result[0].strip()
    base_name = package_token.split("[", 1)[0]
    return package_token, normalize_package_name(base_name)


def sync_mypy_additional_dependencies(
    config_text: str, versions: dict[str, str]
) -> str:
    lines = config_text.splitlines()

    try:
        mypy_repo_idx = next(
            idx for idx, line in enumerate(lines) if line.strip() == MYPY_REPO_MARKER
        )
    except StopIteration as exc:
        raise RuntimeError(
            "mypy repo block was not found in .pre-commit-config.yaml"
        ) from exc

    additional_idx = None
    for idx in range(mypy_repo_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("- repo: ") and idx > mypy_repo_idx:
            break
        if stripped == ADDITIONAL_DEPS_MARKER:
            additional_idx = idx
            break

    if additional_idx is None:
        raise RuntimeError(
            "additional_dependencies block was not found in mypy hook config"
        )

    marker_indent = len(lines[additional_idx]) - len(lines[additional_idx].lstrip(" "))
    dep_indent = " " * (marker_indent + 2)
    dep_prefix = f"{dep_indent}- "

    dep_start = additional_idx + 1
    dep_end = dep_start
    while dep_end < len(lines) and lines[dep_end].startswith(dep_prefix):
        dep_end += 1

    if dep_start == dep_end:
        raise RuntimeError("mypy additional_dependencies list is empty")

    old_dep_lines = lines[dep_start:dep_end]
    new_dep_lines: list[str] = []

    missing_in_requirements: list[str] = []
    for line in old_dep_lines:
        dep_spec = line[len(dep_prefix) :].strip()
        package_token, normalized_package = extract_dep_package(dep_spec)
        if not package_token or not normalized_package:
            raise RuntimeError(f"failed to parse dependency spec: {dep_spec!r}")

        version = versions.get(normalized_package)
        if version is None:
            missing_in_requirements.append(package_token)
            continue

        new_dep_lines.append(f"{dep_prefix}{package_token}=={version}")

    if missing_in_requirements:
        missing = ", ".join(sorted(set(missing_in_requirements)))
        raise RuntimeError(
            "dependencies are missing in infra/requirements/dev.txt: " f"{missing}"
        )

    updated_lines = lines[:dep_start] + new_dep_lines + lines[dep_end:]
    return "\n".join(updated_lines) + "\n"


def main() -> None:
    if not PRECOMMIT_CONFIG_PATH.exists():
        raise SystemExit(f"Missing file: {PRECOMMIT_CONFIG_PATH}")
    if not DEV_REQUIREMENTS_PATH.exists():
        raise SystemExit(f"Missing file: {DEV_REQUIREMENTS_PATH}")

    requirements_text = DEV_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    versions = parse_requirements_versions(requirements_text)
    if not versions:
        raise SystemExit("No pinned dependencies found in infra/requirements/dev.txt")

    original_config = PRECOMMIT_CONFIG_PATH.read_text(encoding="utf-8")
    updated_config = sync_mypy_additional_dependencies(original_config, versions)

    if updated_config != original_config:
        PRECOMMIT_CONFIG_PATH.write_text(updated_config, encoding="utf-8")
        print("Updated mypy additional_dependencies in .pre-commit-config.yaml")
    else:
        print("mypy additional_dependencies are already in sync")


if __name__ == "__main__":
    main()
