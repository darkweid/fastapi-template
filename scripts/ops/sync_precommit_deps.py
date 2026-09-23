from __future__ import annotations

from pathlib import Path
import re

PRECOMMIT_CONFIG_PATH = Path(".pre-commit-config.yaml")
DEV_REQUIREMENTS_PATH = Path("infra/requirements/dev.txt")
SECURITY_REQUIREMENTS_PATH = Path("infra/requirements/security.txt")
MYPY_REPO_MARKER = "- repo: https://github.com/pre-commit/mirrors-mypy"
BANDIT_REPO_MARKER = "- repo: https://github.com/PyCQA/bandit"
ADDITIONAL_DEPS_MARKER = "additional_dependencies:"
# pip-compile keeps requested extras in the lockfile line (taskiq[reload]==...);
# the version pins the base package, so the extras group is matched and dropped.
REQ_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^#\s]+)")
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
    """Split a dependency into the token as written in the pre-commit config,
    extras included, and the normalized name the lockfile is keyed by."""
    dep_spec = dep_spec.strip()
    if not dep_spec:
        return "", ""

    split_result = SPEC_SPLIT_RE.split(dep_spec, maxsplit=1)
    package_token = split_result[0].strip()
    base_name = package_token.split("[", 1)[0]
    return package_token, normalize_package_name(base_name)


def sync_hook_rev(
    config_text: str,
    versions: dict[str, str],
    *,
    repo_marker: str,
    package: str,
    requirements_path: Path,
    tag_prefix: str,
) -> str:
    """Pin one hook's rev to the version the lockfile pins for `package`.

    A rev drifting from the lockfile means pre-commit and CI run different
    releases of the same tool, which shows up as a finding that reproduces in
    one place and not the other. `tag_prefix` is the repository's own tagging
    habit - the mypy mirror tags v<version>, bandit tags <version> - and
    getting it wrong pins a tag that does not exist.
    """
    version = versions.get(normalize_package_name(package))
    if version is None:
        raise RuntimeError(f"{package} is not pinned in {requirements_path}")

    lines = config_text.splitlines()
    try:
        repo_idx = next(
            idx for idx, line in enumerate(lines) if line.strip() == repo_marker
        )
    except StopIteration as exc:
        raise RuntimeError(
            f"{package} repo block was not found in .pre-commit-config.yaml"
        ) from exc

    for idx in range(repo_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("- repo: "):
            break
        if stripped.startswith("rev:"):
            indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip(" "))]
            lines[idx] = f"{indent}rev: {tag_prefix}{version}"
            return "\n".join(lines) + ("\n" if config_text.endswith("\n") else "")

    raise RuntimeError(f"rev line was not found in the {package} repo block")


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
    for requirements_path in (DEV_REQUIREMENTS_PATH, SECURITY_REQUIREMENTS_PATH):
        if not requirements_path.exists():
            raise SystemExit(f"Missing file: {requirements_path}")

    versions = parse_requirements_versions(
        DEV_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    )
    if not versions:
        raise SystemExit("No pinned dependencies found in infra/requirements/dev.txt")

    security_versions = parse_requirements_versions(
        SECURITY_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    )

    original_config = PRECOMMIT_CONFIG_PATH.read_text(encoding="utf-8")
    updated_config = sync_hook_rev(
        original_config,
        versions,
        repo_marker=MYPY_REPO_MARKER,
        package="mypy",
        requirements_path=DEV_REQUIREMENTS_PATH,
        tag_prefix="v",
    )
    updated_config = sync_mypy_additional_dependencies(updated_config, versions)
    updated_config = sync_hook_rev(
        updated_config,
        security_versions,
        repo_marker=BANDIT_REPO_MARKER,
        package="bandit",
        requirements_path=SECURITY_REQUIREMENTS_PATH,
        tag_prefix="",
    )

    if updated_config != original_config:
        PRECOMMIT_CONFIG_PATH.write_text(updated_config, encoding="utf-8")
        print("Updated the pinned hook revs in .pre-commit-config.yaml")
    else:
        print("Hook revs and mypy additional_dependencies are already in sync")


if __name__ == "__main__":
    main()
