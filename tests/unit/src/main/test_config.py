import logging
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.main import config as config_module
from src.main.config import (
    AppConfig,
    CacheConfig,
    JWTConfig,
    find_project_root_robust,
)


def _base_jwt_config_data() -> dict[str, object]:
    return {
        "JWT_USER_SECRET_KEY": "unit-test-user-secret-key-long-enough",
        "JWT_VERIFY_SECRET_KEY": "unit-test-verify-secret-key-long-enough",
        "JWT_ADMIN_SECRET_KEY": "unit-test-admin-secret-key-long-enough",
        "JWT_RESET_PASSWORD_SECRET_KEY": "unit-test-reset-secret-key-long-enough",
        "ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": 15,
        "REFRESH_TOKEN_EXPIRE_MINUTES": 129_600,
        "VERIFICATION_TOKEN_EXPIRE_MINUTES": 180,
        "RESET_PASSWORD_TOKEN_EXPIRE_MINUTES": 180,
    }


def _base_app_config_data() -> dict[str, object]:
    return {
        "VERSION": "1.0.0",
        "DEBUG": False,
        "LOCAL_TIMEZONE": "UTC",
        "LOG_LEVEL": "INFO",
        "LOG_LEVEL_FILE": "WARNING",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
        "CORS_ALLOWED_CREDENTIALS": True,
        "CORS_ALLOWED_METHODS": "*",
        "CORS_ALLOWED_HEADERS": "*",
        "CORS_EXPOSE_HEADERS": "*",
        "TRUST_PROXY_HEADERS": "true",
        "PROJECT_NAME": "app",
        "PROJECT_SECRET_KEY": "unit-test-project-secret-key-long-enough",
        "PING_INTERVAL": 10,
        "CONNECTION_TTL": 10,
    }


def test_parse_cors_list_json_string() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = '["https://a.com", "https://b.com"]'

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == ["https://a.com", "https://b.com"]


def test_parse_cors_list_semicolon_delimiter() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_METHODS"] = "GET;POST;PUT"

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_METHODS == ["GET", "POST", "PUT"]


def test_parse_trust_proxy_hosts_json_string() -> None:
    data = _base_app_config_data()
    data["TRUST_PROXY_HOSTS"] = '["127.0.0.1", "::1", "10.0.0.0/8"]'

    app_config = AppConfig(**data)

    assert app_config.TRUST_PROXY_HOSTS == ["127.0.0.1", "::1", "10.0.0.0/8"]


def test_app_config_reads_cors_allowed_credentials_flag() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_CREDENTIALS"] = False

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_CREDENTIALS is False


def test_app_config_defaults_to_no_cors_origins() -> None:
    data = _base_app_config_data()
    del data["CORS_ALLOWED_ORIGINS"]

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == []


def test_app_config_rejects_wildcard_origin_with_credentials() -> None:
    # Starlette echoes back any Origin when this pair is set, which turns every
    # third-party page into an authenticated client of this API.
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "*"
    data["CORS_ALLOWED_CREDENTIALS"] = True

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS=\\*"):
        AppConfig(**data)


def test_app_config_rejects_wildcard_among_explicit_origins_with_credentials() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "https://app.example.com,*"
    data["CORS_ALLOWED_CREDENTIALS"] = True

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS=\\*"):
        AppConfig(**data)


def test_app_config_allows_wildcard_origin_without_credentials() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "*"
    data["CORS_ALLOWED_CREDENTIALS"] = False

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == ["*"]


def test_find_project_root_robust_finds_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    nested = root / "nested" / "inner"
    nested.mkdir(parents=True)
    (root / "Makefile").write_text("all:")

    collected_logs: list[str] = []

    def fake_info(message: str, *args: object, **kwargs: object) -> None:
        collected_logs.append(message % args if args else message)

    monkeypatch.setattr(config_module, "logger", logging.getLogger("config_test"))
    monkeypatch.setattr(config_module.logger, "info", fake_info)

    result = find_project_root_robust(start_path=nested, max_depth=5)

    assert result == root
    assert any("Project root found" in m for m in collected_logs)


def test_cache_config_defaults() -> None:
    cache_config = CacheConfig()

    assert cache_config.CACHE_ENABLED is True
    assert cache_config.CACHE_DEFAULT_TTL == 60
    assert cache_config.CACHE_VERSION_TTL == 604800
    assert cache_config.CACHE_KEY_PREFIX == "cache"


def test_cache_config_rejects_default_ttl_above_version_ttl() -> None:
    with pytest.raises(ValueError, match="CACHE_VERSION_TTL"):
        CacheConfig(CACHE_DEFAULT_TTL=100, CACHE_VERSION_TTL=50)


def test_jwt_config_rejects_short_secret() -> None:
    with pytest.raises(ValidationError):
        JWTConfig(**{**_base_jwt_config_data(), "JWT_USER_SECRET_KEY": "too-short"})


def test_jwt_config_rejects_unknown_algorithm() -> None:
    with pytest.raises(ValidationError):
        JWTConfig(**{**_base_jwt_config_data(), "ALGORITHM": "none"})


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_jwt_config_accepts_allowlisted_algorithms(algorithm: str) -> None:
    jwt_config = JWTConfig(**{**_base_jwt_config_data(), "ALGORITHM": algorithm})

    assert jwt_config.ALGORITHM == algorithm


def test_app_config_rejects_short_project_secret() -> None:
    data = _base_app_config_data()
    data["PROJECT_SECRET_KEY"] = "short"

    with pytest.raises(ValidationError):
        AppConfig(**data)


def test_app_config_ships_no_docs_credentials_by_default() -> None:
    app_config = AppConfig(**_base_app_config_data())

    assert app_config.DOCS_USERNAME == ""
    assert app_config.DOCS_PASSWORD == ""


def test_find_project_root_robust_returns_start_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = tmp_path / "empty"
    start.mkdir()

    collected_errors: list[str] = []

    def fake_error(message: str, *args: object, **kwargs: object) -> None:
        collected_errors.append(message % args if args else message)

    monkeypatch.setattr(config_module, "logger", logging.getLogger("config_test"))
    monkeypatch.setattr(config_module.logger, "error", fake_error)

    result = find_project_root_robust(start_path=start, max_depth=2)

    assert result == start
    assert any("No project root found" in m for m in collected_errors)
