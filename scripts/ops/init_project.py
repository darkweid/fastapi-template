"""Turns a fresh copy of the template into a named project: compose project,
container, image, volume and network names, optionally the pinned subnet with
the values that move with it, and a `.env` with generated secrets.

Runs once, on the host, before the virtualenv exists: standard library only,
and syntax the macOS system python3 (3.9) still reads."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from urllib.parse import urlparse

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
# a backslash ends or escapes the value; a backtick is refused with them so the
# value is inert in a shell as well. Control characters and Unicode line
# breaks are caught by isprintable().
FORBIDDEN_TITLE_CHARACTERS = re.compile(r'["\\$`]')

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
# Signing secrets, distinct per purpose (docs/readme/bootstrap.md, "Signing
# secrets"). Matched by suffix, so the keys a new realm adds to .env.example
# are generated without touching this script.
SECRET_KEY_SUFFIX = "_SECRET_KEY"  # nosec B105
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
    if (
        FORBIDDEN_TITLE_CHARACTERS.search(title)
        or not title.isprintable()
        or not title.strip()
    ):
        raise InitError(
            'TITLE may not be blank or contain ", \\, $, ` or non-printable '
            "characters"
        )
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
        try:
            original = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise InitError(f"{path}: cannot be read ({error})") from None
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


def build_env(example_text: str) -> str:
    """Fills the secrets and passwords of an (already edited) .env.example and
    keeps every other line, so check_env.py finds each example key in .env."""
    lines = []
    for line in example_text.splitlines(keepends=True):
        key, separator, _ = line.partition("=")
        key = key.strip()
        if not separator or key.startswith("#"):
            lines.append(line)
        elif key.endswith(SECRET_KEY_SUFFIX):
            lines.append(f"{key}={secrets.token_urlsafe(SECRET_BYTES)}\n")
        elif key in PASSWORD_KEYS:
            lines.append(f"{key}={secrets.token_urlsafe(PASSWORD_BYTES)}\n")
        else:
            lines.append(line)
    return "".join(lines)


# What scripts/ops/check_env.py refuses at the first deploy, restated here
# because that module is not importable before the virtualenv exists; a unit
# test holds the two to the same verdict on the generated .env.
DOUBLE_QUOTED_VALUE = re.compile(r'"((?:[^"\\]|\\.)*)"')
SINGLE_QUOTED_VALUE = re.compile(r"'([^']*)'")
INLINE_COMMENT = re.compile(r"\s#")
TRUTHY_VALUES = {"1", "true", "t", "y", "yes", "on"}
S3_KEY_PREFIX = "S3_"
PUBLIC_URL_KEYS = ("PUBLIC_BASE_URL",)
LOCAL_HOSTNAMES = {"localhost", "::1", "0.0.0.0"}  # nosec B104


def _dotenv_entries(text: str) -> dict[str, str]:
    entries = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        value = raw.strip()
        for quoted in (DOUBLE_QUOTED_VALUE, SINGLE_QUOTED_VALUE):
            match = quoted.match(value)
            if match:
                value = match.group(1)
                break
        else:
            value = INLINE_COMMENT.split(raw, maxsplit=1)[0].strip()
        entries[key.strip()] = value
    return entries


def _points_at_localhost(value: str) -> bool:
    hostname = (urlparse(value.strip()).hostname or "").lower()
    return hostname in LOCAL_HOSTNAMES or hostname.startswith("127.")


def pending_before_deploy(env_text: str) -> tuple[str, ...]:
    """One `KEY: reason` line per value the deploy gate would still refuse."""
    entries = _dotenv_entries(env_text)
    s3_enabled = entries.get("S3_ENABLED", "").strip().lower() in TRUTHY_VALUES
    pending = []
    for key, value in sorted(entries.items()):
        dormant = key.startswith(S3_KEY_PREFIX) and key != "S3_ENABLED"
        if PLACEHOLDER_MARKER in value.lower() and not (dormant and not s3_enabled):
            pending.append(f"{key}: still a placeholder")
        if key in PUBLIC_URL_KEYS and _points_at_localhost(value):
            pending.append(f"{key}: points at localhost, set the deployed frontend")
    return tuple(pending)


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
ENV_FILE_MODE = 0o600
REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_STEPS = (
    "If a stack of this checkout ever ran: `docker compose -p fastapi-template "
    "-f infra/docker-compose.yml down` (the old project name) and "
    "`docker network rm app-network` (the old network holds the subnet, so the "
    'next `make run` fails with "Pool overlaps"), then drop the template-* '
    "volumes if nothing in them matters.",
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
    pending: tuple[str, ...]
    env_hints: tuple[str, ...]
    dry_run: bool


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603  # nosec B603 B607
            ["git", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise InitError(f"cannot run git ({error}); install git first") from None


def _ensure_can_run(root: Path) -> None:
    inside = _git(root, "rev-parse", "--show-toplevel")
    if (
        inside.returncode != 0
        or Path(inside.stdout.strip()).resolve() != root.resolve()
    ):
        raise InitError(
            f"{root} is not the root of a git work tree; run this from a clone"
        )
    try:
        compose = (root / COMPOSE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise InitError(f"{COMPOSE_FILE}: cannot be read ({error})") from None
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
    # A dangling symlink reads as absent, and writing through any symlink puts
    # the secrets wherever it points.
    if (root / ENV_FILE).is_symlink():
        raise InitError(f"{ENV_FILE} is a symlink; replace it with a file or remove it")


def _replace_file(target: Path, text: str) -> None:
    """Swaps the file in one rename, so a failure leaves either the old text or
    the new one, never half of it; the mode (deploy.sh is executable) is kept."""
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".init-project"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _create_env(path: Path, text: str) -> None:
    # O_EXCL fails rather than follow a symlink or overwrite a file that
    # appeared since the check.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, ENV_FILE_MODE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
    except BaseException:
        # A truncated .env left in place would read as the developer's own on
        # the next run and never be regenerated.
        path.unlink(missing_ok=True)
        raise


def _write(root: Path, planned: dict[str, str], env_text: str | None) -> None:
    """The compose file goes last: its project name is the initialized marker,
    so a run that fails before it can be reset and run again."""
    order = sorted(planned, key=lambda path: path == COMPOSE_FILE)
    written: list[str] = []
    env_created = False
    try:
        for path in order:
            if path == COMPOSE_FILE and env_text is not None:
                _create_env(root / ENV_FILE, env_text)
                env_created = True
            _replace_file(root / path, planned[path])
            written.append(path)
    except OSError as error:
        # .env is untracked, so `git checkout` would not undo it.
        if env_created:
            (root / ENV_FILE).unlink(missing_ok=True)
        restore = " ".join(written) or "(nothing written yet)"
        raise InitError(
            f"writing failed ({error}); undo the partial run with "
            f"`git checkout -- {restore}` and run it again"
        ) from None


def run(root: Path, options: Options) -> Report:
    _ensure_can_run(root)
    planned = plan_edits(root, options)
    env_exists = (root / ENV_FILE).exists()
    example_text = planned.get(
        ENV_EXAMPLE_FILE, (root / ENV_EXAMPLE_FILE).read_text(encoding="utf-8")
    )
    env_text = build_env(example_text)
    if not options.dry_run:
        _write(root, planned, None if env_exists else env_text)
    return Report(
        edited=tuple(sorted(planned)),
        env_created=not env_exists and not options.dry_run,
        env_exists=env_exists,
        # An existing .env was not written by this run; its values are unknown.
        pending=() if env_exists else pending_before_deploy(env_text),
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
    if report.pending:
        lines.append(f"The deploy gate refuses these in {ENV_FILE}; fix them first:")
        lines.extend(f"  {item}" for item in report.pending)
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
