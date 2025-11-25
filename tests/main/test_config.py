import logging
from pathlib import Path

import pytest

from src.main import config as config_module
from src.main.config import AppConfig, find_project_root_robust


def _base_app_config_data() -> dict[str, object]:
    return {
        "VERSION": "1.0.0",
        "DEBUG": False,
        "LOCAL_TIMEZONE": "UTC",
        "LOG_LEVEL": "INFO",
        "LOG_LEVEL_FILE": "WARNING",
        "CORS_ALLOWED_ORIGINS": "*",
        "CORS_ALLOW_CREDENTIALS": True,
        "CORS_ALLOWED_METHODS": "*",
        "CORS_ALLOWED_HEADERS": "*",
        "CORS_EXPOSE_HEADERS": "*",
        "TRUST_PROXY_HEADERS": "true",
        "PROJECT_NAME": "app",
        "PROJECT_SECRET_KEY": "secret",
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
