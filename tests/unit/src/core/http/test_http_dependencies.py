from collections.abc import Iterator

from pydantic import ValidationError
import pytest

from src.core.http.dependencies import get_user_agent
from src.main.config import HttpConfig, config


@pytest.fixture(autouse=True)
def _fresh_user_agent() -> Iterator[None]:
    get_user_agent.cache_clear()
    yield
    get_user_agent.cache_clear()


def test_a_blank_setting_derives_the_agent_from_the_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.http, "HTTP_USER_AGENT", "")
    monkeypatch.setattr(config.app, "PROJECT_NAME", "Shop API")
    monkeypatch.setattr(config.app, "VERSION", "2.1.0")

    assert get_user_agent() == "shop-api/2.1.0"


def test_a_set_agent_is_sent_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config.http, "HTTP_USER_AGENT", "shop/2 (+https://shop.example)"
    )

    assert get_user_agent() == "shop/2 (+https://shop.example)"


@pytest.mark.parametrize(
    "value", ["shop/1\r\nX-Evil: 1", "shop/1\x00", "магазин/1", "x" * 257]
)
def test_an_agent_that_could_split_a_request_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        HttpConfig(HTTP_USER_AGENT=value)
