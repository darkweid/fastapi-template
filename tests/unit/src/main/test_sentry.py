from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sentry_sdk.integrations.aiohttp import AioHttpIntegration

from src.main.config import config
import src.main.sentry as sentry_module


@pytest.fixture(autouse=True)
def reset_sentry_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sentry_module, "_sentry_initialized", False)


def test_init_sentry_skips_when_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", True)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")

    sentry_module.init_sentry()

    init_mock.assert_not_called()
    assert sentry_module._sentry_initialized is False


def test_init_sentry_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", False)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")

    sentry_module.init_sentry()

    init_mock.assert_not_called()
    assert sentry_module._sentry_initialized is False


def test_init_sentry_skips_when_dsn_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", None)

    sentry_module.init_sentry()

    init_mock.assert_not_called()
    assert sentry_module._sentry_initialized is False


def test_init_sentry_initializes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")
    monkeypatch.setattr(config.sentry, "SENTRY_ENV", "test")
    monkeypatch.setattr(config.app, "VERSION", "1.2.3")

    sentry_module.init_sentry()
    sentry_module.init_sentry()

    init_mock.assert_called_once()
    assert sentry_module._sentry_initialized is True


def test_init_sentry_sends_no_frame_locals_or_request_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locals and bodies of a failing request hold OTP codes and passwords that
    the key-name scrubber misses, and send_default_pii=False does not drop them."""
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")

    sentry_module.init_sentry()

    kwargs = init_mock.call_args.kwargs
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs.get("send_default_pii", False) is False


def test_init_sentry_passes_the_privacy_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process whose requests carry secrets in headers turns off the
    integration Sentry would enable by itself to attach them."""
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")
    disabled = AioHttpIntegration()
    before_send = MagicMock()
    before_send_transaction = MagicMock()

    sentry_module.init_sentry(
        disabled_integrations=(disabled,),
        before_send=before_send,
        before_send_transaction=before_send_transaction,
    )

    kwargs = init_mock.call_args.kwargs
    assert kwargs["disabled_integrations"] == [disabled]
    assert kwargs["before_send"] is before_send
    assert kwargs["before_send_transaction"] is before_send_transaction


def test_init_sentry_disables_nothing_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init_mock)
    monkeypatch.setattr(config.app, "DEBUG", False)
    monkeypatch.setattr(config.app, "TESTING", False)
    monkeypatch.setattr(config.sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config.sentry, "SENTRY_DSN", "http://example.com")

    sentry_module.init_sentry()

    kwargs = init_mock.call_args.kwargs
    assert kwargs["disabled_integrations"] == []
    assert kwargs["before_send"] is None
    assert kwargs["before_send_transaction"] is None
