from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_env import (
    PLACEHOLDER_MARKER,
    SECRET_KEY_PATTERN,
    collect_problems,
    parse_env,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

EXAMPLE = {
    "DEBUG": "false",
    "VALIDATE_CERTS": "True",
    "COOKIE_SECURE": "true",
    "POSTGRES_PASSWORD": "secure_password",
    "PUBLIC_BASE_URL": "http://localhost:3000",
    "JWT_USER_SECRET_KEY": "example-jwt-user-secret-key-not-real",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "EMAIL_USER": "user@example.com",
}


def _deployable_env() -> dict[str, str]:
    return {
        "DEBUG": "false",
        "VALIDATE_CERTS": "True",
        "COOKIE_SECURE": "true",
        "POSTGRES_PASSWORD": "a-real-database-password",
        "PUBLIC_BASE_URL": "https://app.example.com",
        "JWT_USER_SECRET_KEY": "a-real-jwt-user-signing-secret-key",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        "EMAIL_USER": "user@example.com",
    }


def test_deployable_env_has_no_problems() -> None:
    assert collect_problems(EXAMPLE, _deployable_env()) == []


def test_missing_key_is_reported() -> None:
    actual = _deployable_env()
    del actual["COOKIE_SECURE"]

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["Missing keys in .env: COOKIE_SECURE"]


def test_secret_copied_from_example_is_reported() -> None:
    actual = _deployable_env()
    actual["POSTGRES_PASSWORD"] = EXAMPLE["POSTGRES_PASSWORD"]

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        "POSTGRES_PASSWORD still holds the placeholder value from .env.example"
    ]


def test_placeholder_marker_is_reported_even_when_example_changed() -> None:
    actual = _deployable_env()
    actual["JWT_USER_SECRET_KEY"] = "our-own-jwt-user-secret-key-not-real"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        "JWT_USER_SECRET_KEY still holds the placeholder value from .env.example"
    ]


def test_expiry_setting_named_after_a_token_is_not_a_secret() -> None:
    """ACCESS_TOKEN_EXPIRE_MINUTES is meant to keep the example value."""
    actual = _deployable_env()
    actual["ACCESS_TOKEN_EXPIRE_MINUTES"] = EXAMPLE["ACCESS_TOKEN_EXPIRE_MINUTES"]

    assert collect_problems(EXAMPLE, actual) == []


def test_non_secret_value_matching_example_is_allowed() -> None:
    actual = _deployable_env()
    actual["EMAIL_USER"] = EXAMPLE["EMAIL_USER"]

    assert collect_problems(EXAMPLE, actual) == []


def test_debug_enabled_is_reported() -> None:
    actual = _deployable_env()
    actual["DEBUG"] = "true"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["DEBUG=true is not allowed outside local development"]


def test_certificate_validation_disabled_is_reported() -> None:
    actual = _deployable_env()
    actual["VALIDATE_CERTS"] = "False"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["VALIDATE_CERTS=False is not allowed outside local development"]


def test_insecure_cookies_are_reported() -> None:
    actual = _deployable_env()
    actual["COOKIE_SECURE"] = "false"

    problems = collect_problems(EXAMPLE, actual)

    assert problems == ["COOKIE_SECURE=false is not allowed outside local development"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("DEBUG", "on", id="debug-on"),
        pytest.param("DEBUG", "y", id="debug-y"),
        pytest.param("DEBUG", "t", id="debug-t"),
        pytest.param("COOKIE_SECURE", "off", id="cookies-off"),
        pytest.param("COOKIE_SECURE", "n", id="cookies-n"),
        pytest.param("VALIDATE_CERTS", "f", id="certs-f"),
    ],
)
def test_every_boolean_spelling_pydantic_accepts_is_reported(
    key: str, value: str
) -> None:
    """
    pydantic parses on/off/t/f/y/n too, so a check that only knows "true" is a
    gate anyone can walk past by spelling the value differently.
    """
    actual = _deployable_env()
    actual[key] = value

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [f"{key}={value} is not allowed outside local development"]


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://localhost:3000", id="localhost"),
        pytest.param("http://127.0.0.1:8000", id="loopback-ipv4"),
        pytest.param("http://[::1]:3000", id="loopback-ipv6"),
        pytest.param("http://0.0.0.0:3000", id="unspecified"),  # noqa: S104
    ],
)
def test_local_public_base_url_is_reported(url: str) -> None:
    actual = _deployable_env()
    actual["PUBLIC_BASE_URL"] = url

    problems = collect_problems(EXAMPLE, actual)

    assert problems == [
        f"PUBLIC_BASE_URL={url} points at localhost: users receive email links "
        "they cannot open"
    ]


def test_public_base_url_on_a_real_host_is_allowed() -> None:
    actual = _deployable_env()
    actual["PUBLIC_BASE_URL"] = "https://app.example.com"

    assert collect_problems(EXAMPLE, actual) == []


def test_shipped_env_example_is_rejected_as_a_deploy_config() -> None:
    """
    The example is a working local config, never a deployable one. Copying it
    unchanged has to fail loudly, and every secret in it has to be named.
    """
    example = parse_env(REPO_ROOT / ".env.example")

    problems = collect_problems(example, example)

    flagged = {problem.split()[0].split("=")[0] for problem in problems}
    assert {
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "EMAIL_PASSWORD",
        "DOCS_PASSWORD",
        "PROJECT_SECRET_KEY",
        "CSRF_SECRET_KEY",
        "JWT_USER_SECRET_KEY",
        "JWT_ADMIN_SECRET_KEY",
        "JWT_VERIFY_SECRET_KEY",
        "JWT_RESET_PASSWORD_SECRET_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "SENTRY_DSN",
        "PUBLIC_BASE_URL",
    } <= flagged


def test_every_secret_in_the_example_carries_the_placeholder_marker() -> None:
    """
    Equality with .env.example is not enough on its own: editing the example is
    the first thing a fork does, and that would silently disable the check.
    """
    example = parse_env(REPO_ROOT / ".env.example")

    unmarked = [
        key
        for key, value in example.items()
        if SECRET_KEY_PATTERN.search(key) and PLACEHOLDER_MARKER not in value.lower()
    ]

    assert unmarked == []


def test_parse_env_reads_quotes_comments_and_embedded_equals(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value",
                'QUOTED="quoted value"',
                "SINGLE='single'",
                "  SPACED  =  padded  ",
                "DSN=postgresql://user:pass@host:5432/db?ssl=require",
                "NOT_A_PAIR",
            ]
        )
    )

    assert parse_env(env_file) == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "SPACED": "padded",
        "DSN": "postgresql://user:pass@host:5432/db?ssl=require",
    }
