"""Turns a fresh copy of the template into a named project: compose project,
container, image, volume and network names, optionally the pinned subnet with
the values that move with it, and a `.env` with generated secrets.

Runs once, on the host, before the virtualenv exists: standard library only,
and syntax the macOS system python3 (3.9) still reads."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import secrets
import subprocess  # nosec B404
import sys

DEFAULT_SUBNET = ipaddress.IPv4Network("172.30.0.0/24")
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SLUG_MIN_LENGTH = 2
SLUG_MAX_LENGTH = 30
SUBNET_PREFIX_LENGTH = 24
PRIVATE_IPV4_RANGES = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
# docker0 takes this range on every Docker host; a project network inside it
# fails to start or shadows the default bridge.
DOCKER_DEFAULT_BRIDGE = ipaddress.IPv4Network("172.17.0.0/16")
# The value has to stay equal to scripts/ops/check_env.py PLACEHOLDER_MARKER;
# a unit test holds them together.
PLACEHOLDER_MARKER = "-not-real"
# Inside double quotes compose's dotenv reader interpolates `$`, and a quote or
# a backslash ends or escapes the value.
FORBIDDEN_TITLE_CHARACTERS = re.compile(r'["\\$\x00-\x1f\x7f]')

COMPOSE_FILE = "infra/docker-compose.yml"
ENV_EXAMPLE_FILE = ".env.example"
TEMPLATE_PROJECT_NAME = "fastapi-template"
NAME_PREFIX = "template-"
# How many `template-` names each file carries today. A different count means
# the template moved on without this script; renaming the rest would leave one
# container, image or volume colliding with every other fork on the host.
NAME_PREFIX_COUNTS = {
    COMPOSE_FILE: 14,
    "infra/docker-compose.override.yml": 4,
    "infra/docker-compose.test.yml": 3,
    "infra/deploy/deploy.sh": 1,
    "Makefile": 1,
}
# Files holding an address of the default subnet: the network itself, the
# TRUST_PROXY_HOSTS default and its example, and the gateway nginx denies on
# the probes. They move together (see the comment on app-network in compose).
SUBNET_ADDRESS_COUNTS = {
    COMPOSE_FILE: 3,
    "src/main/config.py": 1,
    ENV_EXAMPLE_FILE: 3,
    "infra/nginx/proxy.inc": 1,
}
# Only the Docker name: the compose key `app-network` is what services, tests
# and docs refer to, and it never leaves the project.
NETWORK_NAME_LINE = "    name: app-network\n"
PROJECT_NAME_LINE = re.compile(r"^PROJECT_NAME=.*$", re.MULTILINE)
# The three addresses of the default subnet, never a prefix of a longer one
# (172.30.0.1 is a prefix of 172.30.0.128).
DEFAULT_SUBNET_ADDRESS = re.compile(r"(?<![\d.])172\.30\.0\.(0/24|128/25|1)(?![\d./])")
# Signing secrets: distinct per purpose (docs/readme/bootstrap.md, "Signing secrets").
SECRET_KEYS = (
    "JWT_USER_SECRET_KEY",
    "JWT_USER_VERIFY_SECRET_KEY",
    "JWT_USER_RESET_PASSWORD_SECRET_KEY",
    "CSRF_SECRET_KEY",
)
# token_urlsafe is ASCII, which DOCS_PASSWORD needs (HTTP Basic reaches FastAPI
# as ASCII), and 32 bytes encode to 43 characters, above SECRET_MIN_LENGTH.
PASSWORD_KEYS = ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "DOCS_PASSWORD")
SECRET_BYTES = 48
PASSWORD_BYTES = 32
EDITED_FILES = tuple(sorted(set(NAME_PREFIX_COUNTS) | set(SUBNET_ADDRESS_COUNTS)))


class InitError(Exception):
    """A refusal the user can act on; main() prints it and exits 1."""


def validate_name(name: str) -> str:
    # The pattern alone accepts a single letter, hence the separate minimum.
    if (
        not SLUG_PATTERN.match(name)
        or not SLUG_MIN_LENGTH <= len(name) <= SLUG_MAX_LENGTH
    ):
        raise InitError(
            f"NAME must be {SLUG_MIN_LENGTH}-{SLUG_MAX_LENGTH} characters of lowercase "
            f"letters, digits and single inner hyphens, starting with a letter: {name!r}"
        )
    if "template" in name:
        raise InitError(
            "NAME must not contain 'template': the already-initialized check "
            "looks for the template's own name"
        )
    return name


def parse_subnet(text: str | None) -> ipaddress.IPv4Network:
    if not text:
        return DEFAULT_SUBNET
    try:
        subnet = ipaddress.ip_network(text, strict=True)
    except ValueError as error:
        raise InitError(
            f"SUBNET is not a network address: {text!r} ({error})"
        ) from None
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or subnet.prefixlen != SUBNET_PREFIX_LENGTH
    ):
        raise InitError(f"SUBNET must be an IPv4 /{SUBNET_PREFIX_LENGTH}: {text!r}")
    if not any(subnet.subnet_of(private) for private in PRIVATE_IPV4_RANGES):
        raise InitError(
            f"SUBNET must be private (10/8, 172.16/12, 192.168/16): {text!r}"
        )
    if subnet.subnet_of(DOCKER_DEFAULT_BRIDGE):
        raise InitError(f"SUBNET overlaps docker0 ({DOCKER_DEFAULT_BRIDGE}): {text!r}")
    return subnet


def validate_title(title: str | None) -> str | None:
    if not title:
        return None
    if FORBIDDEN_TITLE_CHARACTERS.search(title):
        raise InitError('TITLE may not contain ", \\, $ or control characters')
    return title


@dataclass(frozen=True)
class Options:
    name: str
    title: str | None
    subnet: ipaddress.IPv4Network
    dry_run: bool


def gateway_address(subnet: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    return subnet.network_address + 1


def trusted_proxy_range(subnet: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
    """The upper half of the subnet: where compose hands out container
    addresses, the gateway left outside it."""
    return ipaddress.IPv4Network((int(subnet.network_address) + 128, 25))


def _replace_counted(path: str, text: str, old: str, new: str, expected: int) -> str:
    found = text.count(old)
    if found != expected:
        raise InitError(
            f"{path}: expected {expected} occurrence(s) of {old.strip()!r}, "
            f"found {found}; update scripts/ops/init_project.py before running it"
        )
    return text.replace(old, new)


def _move_subnet(path: str, text: str, subnet: ipaddress.IPv4Network) -> str:
    replacements = {
        "0/24": str(subnet),
        "128/25": str(trusted_proxy_range(subnet)),
        "1": str(gateway_address(subnet)),
    }
    moved, found = DEFAULT_SUBNET_ADDRESS.subn(
        lambda match: replacements[match.group(1)], text
    )
    expected = SUBNET_ADDRESS_COUNTS[path]
    if found != expected:
        raise InitError(
            f"{path}: expected {expected} address(es) of {DEFAULT_SUBNET}, "
            f"found {found}; update scripts/ops/init_project.py before running it"
        )
    return moved


def plan_edits(root: Path, options: Options) -> dict[str, str]:
    """Computes every edit before any write, so a drifted file aborts the run
    with the tree untouched.

    Returns the new text of each file whose text changes, keyed by its path
    relative to `root`."""
    planned: dict[str, str] = {}
    for path in EDITED_FILES:
        original = (root / path).read_text(encoding="utf-8")
        text = original
        # `fastapi-template` holds no `template-`, so this leaves the project
        # name to its own counted replacement below.
        if path in NAME_PREFIX_COUNTS:
            text = _replace_counted(
                path, text, NAME_PREFIX, f"{options.name}-", NAME_PREFIX_COUNTS[path]
            )
        if path == COMPOSE_FILE:
            # The surrounding newlines keep any indented `name:` from matching.
            text = _replace_counted(
                path,
                text,
                f"\nname: {TEMPLATE_PROJECT_NAME}\n",
                f"\nname: {options.name}\n",
                1,
            )
            text = _replace_counted(
                path, text, NETWORK_NAME_LINE, f"    name: {options.name}-network\n", 1
            )
        if path in SUBNET_ADDRESS_COUNTS and options.subnet != DEFAULT_SUBNET:
            text = _move_subnet(path, text, options.subnet)
        if path == ENV_EXAMPLE_FILE and options.title is not None:
            # A literal replacement only because validate_title rejected `\`.
            text, found = PROJECT_NAME_LINE.subn(
                f'PROJECT_NAME="{options.title}"', text
            )
            if found != 1:
                raise InitError(
                    f"{path}: expected one PROJECT_NAME line, found {found}"
                )
        if text != original:
            planned[path] = text
    return planned


def build_env(example_text: str) -> tuple[str, tuple[str, ...]]:
    """Fills the secrets and passwords of an (already edited) .env.example and
    keeps every other line, so check_env.py finds each example key in .env.

    Returns the .env text and the keys whose value is still a placeholder."""
    lines = []
    remaining = []
    for line in example_text.splitlines(keepends=True):
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key.startswith("#"):
            lines.append(line)
        elif key in SECRET_KEYS:
            lines.append(f"{key}={secrets.token_urlsafe(SECRET_BYTES)}\n")
        elif key in PASSWORD_KEYS:
            lines.append(f"{key}={secrets.token_urlsafe(PASSWORD_BYTES)}\n")
        else:
            if PLACEHOLDER_MARKER in value:
                remaining.append(key)
            lines.append(line)
    return "".join(lines), tuple(remaining)


def env_hints(options: Options) -> tuple[str, ...]:
    """The lines an existing .env, which the run never touches, needs by hand."""
    hints = []
    if options.title is not None:
        hints.append(f'PROJECT_NAME="{options.title}"')
    if options.subnet != DEFAULT_SUBNET:
        hints.append(
            f'TRUST_PROXY_HOSTS=["127.0.0.1","::1","{trusted_proxy_range(options.subnet)}"]'
        )
    return tuple(hints)


ENV_FILE = ".env"
REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_STEPS = (
    "If a stack of this checkout ever ran: `docker compose -p fastapi-template "
    "-f infra/docker-compose.yml down` (the old project name), then drop the "
    "template-* volumes if nothing in them matters.",
    "README badges and links point at darkweid/fastapi-template: swap or delete them.",
    "LICENSE: replace the copyright holder, or delete it for a private project.",
    "infra/ansible/roles/*/meta/main.yml carry `author: fastapi-template`.",
    "Review the diff (`git diff`) and commit it.",
)


@dataclass(frozen=True)
class Report:
    edited: tuple[str, ...]
    env_created: bool
    env_exists: bool
    remaining_placeholders: tuple[str, ...]
    env_hints: tuple[str, ...]
    dry_run: bool


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def _ensure_can_run(root: Path) -> None:
    inside = _git(root, "rev-parse", "--show-toplevel")
    if (
        inside.returncode != 0
        or Path(inside.stdout.strip()).resolve() != root.resolve()
    ):
        raise InitError(
            f"{root} is not the root of a git work tree; run this from a clone"
        )
    compose = (root / COMPOSE_FILE).read_text(encoding="utf-8")
    if f"\nname: {TEMPLATE_PROJECT_NAME}\n" not in compose:
        raise InitError(
            f"already initialized: {COMPOSE_FILE} no longer names the project "
            f"{TEMPLATE_PROJECT_NAME}"
        )
    # .env is gitignored, so --untracked-files=no never reports it and an
    # existing one never blocks the run.
    dirty = _git(
        root, "status", "--porcelain", "--untracked-files=no", "--", *EDITED_FILES
    )
    if dirty.returncode != 0:
        raise InitError(f"git status failed: {dirty.stderr.strip()}")
    if dirty.stdout.strip():
        raise InitError(
            "commit or stash the uncommitted changes in the files this edits first:\n"
            + dirty.stdout.rstrip()
        )


def run(root: Path, options: Options) -> Report:
    _ensure_can_run(root)
    planned = plan_edits(root, options)
    env_path = root / ENV_FILE
    env_exists = env_path.exists()
    example_text = planned.get(
        ENV_EXAMPLE_FILE, (root / ENV_EXAMPLE_FILE).read_text(encoding="utf-8")
    )
    env_text, remaining = build_env(example_text)
    if not options.dry_run:
        for path, text in planned.items():
            (root / path).write_text(text, encoding="utf-8")
        if not env_exists:
            env_path.write_text(env_text, encoding="utf-8")
    return Report(
        edited=tuple(sorted(planned)),
        env_created=not env_exists and not options.dry_run,
        env_exists=env_exists,
        remaining_placeholders=remaining,
        env_hints=env_hints(options) if env_exists else (),
        dry_run=options.dry_run,
    )


def format_report(report: Report) -> str:
    verb = "Would edit" if report.dry_run else "Edited"
    lines = [f"{verb}:", *(f"  {path}" for path in report.edited)]
    if report.env_exists:
        lines.append(f"{ENV_FILE} already exists and was left alone.")
        if report.env_hints:
            lines.append("Copy these values into it:")
            lines.extend(f"  {hint}" for hint in report.env_hints)
    else:
        created = "Would create" if report.dry_run else "Created"
        lines.append(f"{created} {ENV_FILE} with generated secrets and passwords.")
    if report.remaining_placeholders:
        lines.append(f"Still placeholders in {ENV_FILE} (fill before deploying):")
        lines.extend(f"  {key}" for key in report.remaining_placeholders)
    lines.append("Left to do by hand:")
    lines.extend(f"  - {step}" for step in MANUAL_STEPS)
    return "\n".join(lines)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--title")
    parser.add_argument("--subnet")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        options = Options(
            name=validate_name(arguments.name),
            title=validate_title(arguments.title),
            subnet=parse_subnet(arguments.subnet),
            dry_run=arguments.dry_run,
        )
        report = run(arguments.root, options)
    except InitError as error:
        print(f"init-project: {error}", file=sys.stderr)
        return 1
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
