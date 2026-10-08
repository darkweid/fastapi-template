from __future__ import annotations

import ipaddress

import pytest

from scripts.ops import check_env
from scripts.ops.init_project import (
    DEFAULT_SUBNET,
    PLACEHOLDER_MARKER,
    InitError,
    parse_subnet,
    validate_name,
    validate_title,
)


@pytest.mark.parametrize("name", ["myapp", "my-app", "a1", "shop-v2-api", "a" * 30])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a",
        "MyApp",
        "my_app",
        "my-",
        "-my",
        "my--app",
        "1app",
        "my app",
        "a" * 31,
        "my-template",
        "templates",
    ],
)
def test_name_rules(name: str) -> None:
    """Every name becomes a container, volume and network prefix; `my-` would
    produce `my--postgres`, and a name holding `template` defeats the
    already-initialized check on the next run."""
    with pytest.raises(InitError):
        validate_name(name)


def test_blank_subnet_keeps_the_default() -> None:
    assert parse_subnet(None) == DEFAULT_SUBNET
    assert parse_subnet("") == DEFAULT_SUBNET


@pytest.mark.parametrize("text", ["10.42.7.0/24", "172.20.5.0/24", "192.168.77.0/24"])
def test_private_slash_24_subnets_are_accepted(text: str) -> None:
    assert parse_subnet(text) == ipaddress.IPv4Network(text)


@pytest.mark.parametrize(
    "text",
    [
        "10.1.2.5/24",  # host bits set
        "10.1.0.0/16",  # not a /24
        "8.8.8.0/24",  # public
        "172.17.3.0/24",  # docker0
        "fd00::/64",  # IPv6
        "not-a-network",
    ],
)
def test_subnet_rules(text: str) -> None:
    with pytest.raises(InitError):
        parse_subnet(text)


def test_title_is_optional() -> None:
    assert validate_title(None) is None
    assert validate_title("") is None
    assert validate_title("Shop API #2") == "Shop API #2"


@pytest.mark.parametrize(
    "title", ['Say "hi"', "back\\slash", "cost $HOME", "line\nbreak", "tab\there"]
)
def test_title_rules(title: str) -> None:
    """The title lands inside double quotes in a dotenv file, where compose
    interpolates `$` and a quote or backslash ends or escapes the value."""
    with pytest.raises(InitError):
        validate_title(title)


def test_placeholder_marker_matches_the_deploy_gate() -> None:
    """The summary lists what is still a placeholder by the same marker
    check_env.py refuses; the script cannot import it, since it runs before
    the repository root is importable."""
    assert PLACEHOLDER_MARKER == check_env.PLACEHOLDER_MARKER
