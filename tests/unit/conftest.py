import os

import pytest

from src.main.config import Config

# `TESTING` is what selects `.env.test`, so it is the one config key a test must
# keep: dropping it would send `get_settings()` back to the developer's `.env`.
_KEPT_ENV_KEYS = frozenset({"TESTING"})


def _config_env_keys() -> frozenset[str]:
    """Every environment variable the config sections read."""
    return (
        frozenset(
            name
            for section in Config.model_fields.values()
            for name in section.annotation.model_fields  # type: ignore[union-attr]
        )
        - _KEPT_ENV_KEYS
    )


@pytest.fixture(autouse=True)
def _isolated_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Hide config environment variables from the unit suite.

    Config sections are `BaseSettings`, so they read the environment themselves -
    including when a test constructs one directly. Without this, a key exported in
    the developer's shell (`DEBUG`, `COOKIE_SECURE`, `CACHE_DEFAULT_TTL`, ...)
    would override `.env.test` and decide whether a test passes on that machine.
    A test that needs a variable sets it itself; the integration suite keeps its
    own environment, which is why this lives under `tests/unit/` only.
    """
    for name in _config_env_keys():
        monkeypatch.delenv(name, raising=False)


def test_environment_is_isolated_from_the_shell() -> None:
    """The fixture above is what every other unit test relies on; prove it works."""
    assert "DEBUG" not in os.environ
    assert os.environ.get("TESTING") == "true"
