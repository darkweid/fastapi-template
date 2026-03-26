import logging

from fastapi import FastAPI
import pytest

from src.main import route_logging


@pytest.fixture(autouse=True)
def _patch_route_logger(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    logger = logging.getLogger("route_logging_test")
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    monkeypatch.setattr(route_logging, "logger", logger)
    return logger


def test_is_docs_route_detection() -> None:
    class DummyRoute:
        def __init__(self, path: str, name: str):
            self.path = path
            self.name = name

    assert route_logging._is_docs_route(DummyRoute("/openapi.json", "openapi"))
    assert route_logging._is_docs_route(DummyRoute("/docs", "swagger_ui_html"))
    assert route_logging._is_docs_route(
        DummyRoute("/docs/oauth2-redirect", "swagger_ui_redirect")
    )
    assert route_logging._is_docs_route(DummyRoute("/redoc", "redoc_html"))
    assert not route_logging._is_docs_route(DummyRoute("/items", "get_items"))


def test_log_routes_summary_skips_docs_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    @app.get("/items", tags=["Items"])
    async def get_items() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/items", tags=["Items"])
    async def create_item() -> dict[str, str]:
        return {"created": "yes"}

    caplog.set_level(logging.DEBUG, logger="route_logging_test")

    route_logging.log_routes_summary(app, include_debug_list=True)

    info_messages = [
        record.message for record in caplog.records if record.levelno == logging.INFO
    ]
    debug_messages = [
        record.message for record in caplog.records if record.levelno == logging.DEBUG
    ]

    assert any("total=2" in message for message in info_messages)
    assert any(
        "'GET': 1" in message and "'POST': 1" in message for message in info_messages
    )
    assert any("tags={'Items': 2}" in message for message in info_messages)

    assert any("Route: GET /items -> get_items" in msg for msg in debug_messages)
    assert any("Route: POST /items -> create_item" in msg for msg in debug_messages)
