from __future__ import annotations

import ipaddress
from pathlib import Path
import shutil

import pytest

from scripts.ops import check_env
from scripts.ops.check_env import parse_env
from scripts.ops.init_project import (
    COMPOSE_FILE,
    DEFAULT_SUBNET,
    EDITED_FILES,
    PASSWORD_KEYS,
    PLACEHOLDER_MARKER,
    SECRET_KEYS,
    InitError,
    Options,
    build_env,
    env_hints,
    parse_subnet,
    plan_edits,
    validate_name,
    validate_title,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SECRET_MIN_LENGTH = 32  # src/main/config.py SECRET_MIN_LENGTH


def _options(
    name: str = "myapp",
    title: str | None = None,
    subnet: str | None = None,
    dry_run: bool = False,
) -> Options:
    return Options(name=name, title=title, subnet=parse_subnet(subnet), dry_run=dry_run)


@pytest.fixture
def template_files(tmp_path: Path) -> Path:
    for relative in EDITED_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    return tmp_path


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


def test_rename_leaves_no_template_name(template_files: Path) -> None:
    planned = plan_edits(template_files, _options())

    for relative, text in planned.items():
        assert "fastapi-template" not in text, relative
        assert "template-" not in text, relative
    compose = planned[COMPOSE_FILE]
    assert "\nname: myapp\n" in compose
    assert "container_name: myapp-postgres" in compose
    assert "    name: myapp-network\n" in compose
    assert "  app-network:\n" in compose
    assert "myapp-test-$$$$" in planned["Makefile"]


def test_default_subnet_leaves_the_addresses_alone(template_files: Path) -> None:
    planned = plan_edits(template_files, _options())

    assert "src/main/config.py" not in planned
    assert "infra/nginx/proxy.inc" not in planned
    assert "subnet: 172.30.0.0/24" in planned[COMPOSE_FILE]


def test_subnet_moves_every_value_that_names_it(template_files: Path) -> None:
    planned = plan_edits(template_files, _options(subnet="10.42.7.0/24"))

    compose = planned[COMPOSE_FILE]
    assert "subnet: 10.42.7.0/24" in compose
    assert "gateway: 10.42.7.1\n" in compose
    assert "ip_range: 10.42.7.128/25" in compose
    assert '"10.42.7.128/25"' in planned["src/main/config.py"]
    assert (
        'TRUST_PROXY_HOSTS=["127.0.0.1","::1","10.42.7.128/25"]'
        in planned[".env.example"]
    )
    assert "10.42.7.1," in planned[".env.example"]
    assert "deny 10.42.7.1;" in planned["infra/nginx/proxy.inc"]
    for text in planned.values():
        assert "172.30.0." not in text


def test_title_becomes_the_example_project_name(template_files: Path) -> None:
    planned = plan_edits(template_files, _options(title="Shop API #2"))

    assert '\nPROJECT_NAME="Shop API #2"\n' in "\n" + planned[".env.example"]


def test_drifted_count_fails_before_writing(template_files: Path) -> None:
    """A template- name added to a file the script edits must stop the run, or
    the fork keeps one container or volume colliding with every other fork."""
    compose = template_files / COMPOSE_FILE
    compose.write_text(compose.read_text() + "# template-extra\n")

    with pytest.raises(InitError, match="infra/docker-compose.yml"):
        plan_edits(template_files, _options())


def _parsed(text: str, tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "generated.env"
    path.write_text(text, encoding="utf-8")
    return parse_env(path)


def test_env_gets_distinct_secrets_and_passwords(tmp_path: Path) -> None:
    """Sharing one value across purposes lets a token minted for one pass the
    check of another; CSRF_SECRET_KEY is outside the startup validator's reach."""
    text, _ = build_env((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    env = _parsed(text, tmp_path)

    generated = [env[key] for key in (*SECRET_KEYS, *PASSWORD_KEYS)]
    assert len(set(generated)) == len(generated)
    for value in generated:
        assert len(value) >= SECRET_MIN_LENGTH
        assert value.isascii()
        assert PLACEHOLDER_MARKER not in value


def test_env_lists_what_is_still_a_placeholder(tmp_path: Path) -> None:
    text, remaining = build_env(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    )
    env = _parsed(text, tmp_path)

    assert "EMAIL_PASSWORD" in remaining
    assert "SENTRY_DSN" in remaining
    assert not set(remaining) & {*SECRET_KEYS, *PASSWORD_KEYS}
    assert set(remaining) == {
        key for key, value in env.items() if PLACEHOLDER_MARKER in value
    }


def test_env_keeps_every_example_key(tmp_path: Path) -> None:
    """check_env.py fails a deploy on any .env.example key missing from .env."""
    example_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    text, _ = build_env(example_text)

    assert _parsed(text, tmp_path).keys() == _parsed(example_text, tmp_path).keys()


def test_hints_name_what_an_existing_env_needs() -> None:
    options = Options(
        name="myapp", title="Shop", subnet=parse_subnet("10.42.7.0/24"), dry_run=False
    )

    assert env_hints(options) == (
        'PROJECT_NAME="Shop"',
        'TRUST_PROXY_HOSTS=["127.0.0.1","::1","10.42.7.128/25"]',
    )
    assert (
        env_hints(
            Options(name="myapp", title=None, subnet=DEFAULT_SUBNET, dry_run=False)
        )
        == ()
    )
