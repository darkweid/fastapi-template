import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.database.engine import engine, tasks_engine


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
