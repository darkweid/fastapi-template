from __future__ import annotations

from scripts.check_env import collect_problems

EXAMPLE = {
    "DEBUG": "false",
    "VALIDATE_CERTS": "True",
    "COOKIE_SECURE": "true",
    "POSTGRES_PASSWORD": "secure_password",
    "JWT_USER_SECRET_KEY": "example-jwt-user-secret-key-not-real",
    "EMAIL_USER": "user@example.com",
}


def _deployable_env() -> dict[str, str]:
    return {
        "DEBUG": "false",
        "VALIDATE_CERTS": "True",
        "COOKIE_SECURE": "true",
        "POSTGRES_PASSWORD": "a-real-database-password",
        "JWT_USER_SECRET_KEY": "a-real-jwt-user-signing-secret-key",
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
