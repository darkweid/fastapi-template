import ssl
from typing import Any
from unittest.mock import MagicMock

from asyncpg.exceptions import InvalidPasswordError
import pytest
from sqlalchemy import event
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.database.engine import (
    connect_or_operational_error,
    engine,
    tasks_engine,
)


@pytest.mark.parametrize("built", [engine, tasks_engine], ids=["api", "tasks"])
def test_engine_hides_bound_parameters(built: AsyncEngine) -> None:
    """A driver error string reaches logs and Sentry; with parameters shown it
    carries phone numbers, emails and password hashes."""
    assert built.sync_engine.hide_parameters is True


def test_hidden_parameters_stay_out_of_the_error_message() -> None:
    """What hide_parameters buys: the SQL stays readable, the values do not."""
    error = OperationalError(
        "SELECT * FROM clients WHERE phone = $1",
        ("+998901234567",),
        Exception("connection lost"),
        hide_parameters=True,
    )

    assert "+998901234567" not in str(error)
    assert "SELECT * FROM clients" in str(error)


def connect(error: Exception) -> Any:
    dialect = MagicMock()
    dialect.connect.side_effect = error
    dialect.loaded_dbapi = AsyncAdapt_asyncpg_dbapi(MagicMock())
    return connect_or_operational_error(dialect, None, [], {})


@pytest.mark.parametrize("built", [engine, tasks_engine], ids=["api", "tasks"])
def test_engine_names_an_unreachable_server(built: AsyncEngine) -> None:
    """Without the listener a refused connection leaves as a bare OSError, which
    the middleware cannot tell from a failed SMTP call and answers 500."""
    assert event.contains(built.sync_engine, "do_connect", connect_or_operational_error)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("10.0.0.5:5432"),
        TimeoutError("10.0.0.5:5432"),
        OSError("10.0.0.5:5432"),
    ],
)
def test_an_unreachable_server_is_named_by_type_only(error: OSError) -> None:
    with pytest.raises(AsyncAdapt_asyncpg_dbapi.OperationalError) as caught:
        connect(error)

    assert str(caught.value) == type(error).__name__
    assert caught.value.__cause__ is error


def test_a_certificate_that_does_not_verify_keeps_its_own_class() -> None:
    """An OSError as well, but a 503 would say "try later" to a misconfiguration."""
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")

    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        connect(error)

    assert caught.value is error


def test_a_failed_handshake_is_an_unreachable_server() -> None:
    with pytest.raises(AsyncAdapt_asyncpg_dbapi.OperationalError):
        connect(ssl.SSLError(1, "unexpected eof"))


def test_a_refused_login_keeps_its_own_class() -> None:
    with pytest.raises(InvalidPasswordError):
        connect(InvalidPasswordError("password authentication failed"))
