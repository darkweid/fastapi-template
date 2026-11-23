from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.types import Event, Hint
from sqlalchemy.exc import DBAPIError

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
    [always, requested] = kwargs["disabled_integrations"]
    assert isinstance(always, AioHttpIntegration)
    assert requested is disabled
    event: Event = {"message": "text"}
    hint: Hint = {}
    assert kwargs["before_send"](event, hint) is before_send.return_value
    before_send.assert_called_once_with(event, hint)
    assert kwargs["before_send_transaction"] is before_send_transaction


def test_init_sentry_disables_only_the_aiohttp_integration_by_default(
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
    [disabled] = kwargs["disabled_integrations"]
    assert isinstance(disabled, AioHttpIntegration)
    event: Event = {"message": "text"}
    assert kwargs["before_send"](event, {}) is event
    assert kwargs["before_send_transaction"] is None


ECHOING = DBAPIError("SELECT 1", None, ValueError("secret@example.com"))


def echoing_hint() -> Hint:
    return {"exc_info": (DBAPIError, ECHOING, None)}


def echoing_event() -> Event:
    return {
        "exception": {"values": [{"type": "DBAPIError", "value": str(ECHOING)}]},
        "extra": {"scope": "kept"},
    }


def test_what_the_callers_hook_returns_is_scrubbed() -> None:
    """The hook may put the text of the error back."""

    def rewrite(_: Event, __: Hint) -> Event:
        return echoing_event()

    sent = sentry_module._scrubbing(rewrite)({}, echoing_hint())

    assert sent is not None
    assert "secret@example.com" not in str(sent)
    assert sent["extra"] == {"scope": "kept"}


def test_the_callers_hook_still_drops_an_event() -> None:
    def drop(_: Event, __: Hint) -> None:
        return None

    assert sentry_module._scrubbing(drop)(echoing_event(), echoing_hint()) is None


def test_the_callers_hook_is_given_the_original_error() -> None:
    """A hook that drops events by exception class goes on doing it."""
    hook = MagicMock(return_value=None)
    hint = echoing_hint()

    sentry_module._scrubbing(hook)(echoing_event(), hint)

    assert hook.call_args.args[1]["exc_info"][1] is ECHOING


def test_an_event_with_no_exception_part_of_its_own_gets_the_stand_in() -> None:
    sent = sentry_module._scrubbing(None)({}, echoing_hint())

    assert sent is not None
    assert sent["exception"]["values"] == [
        {
            "type": "ScrubbedError",
            "module": "src.core.errors.scrubbed",
            "value": "DBAPIError driver=ValueError",
        }
    ]
