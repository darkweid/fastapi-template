"""Turns a fresh copy of the template into a named project: compose project,
container, image, volume and network names, optionally the pinned subnet with
the values that move with it, and a `.env` with generated secrets.

Runs once, on the host, before the virtualenv exists: standard library only,
and syntax the macOS system python3 (3.9) still reads."""

from __future__ import annotations

import ipaddress
import re

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
