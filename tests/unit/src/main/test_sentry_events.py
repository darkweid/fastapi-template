"""What the SDK really sends, from a process of its own.

Sentry patches Starlette, FastAPI and aiohttp for the whole process and cannot be
taken back, so nothing here may run inside the suite's own.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
RUN_TIMEOUT_SECONDS = 60

# The transport is replaced and nothing else: the options come from init_sentry().
PRELUDE = """
import json
import sentry_sdk
from sentry_sdk.transport import Transport
from src.main.config import config

events = []

class Recorder(Transport):
    def capture_envelope(self, envelope):
        events.extend(item.payload.json for item in envelope.items if item.type == "event")

config.app.TESTING = False
config.app.DEBUG = False
config.sentry.SENTRY_ENABLED = True
config.sentry.SENTRY_DSN = "https://key@sentry.invalid/1"
original_init = sentry_sdk.init
sentry_sdk.init = lambda **options: original_init(**options, transport=Recorder)
"""

APPLICATION = """
from fastapi.testclient import TestClient
import src.main.web as web
from src.core.errors.exceptions import InfrastructureException, ServiceUnavailableException
from src.system.dependencies import get_readiness_service

class NotReady:
    async def ensure_ready(self):
        raise ServiceUnavailableException("down")

async def infrastructure():
    raise InfrastructureException("S3 is disabled")

async def unexpected():
    raise RuntimeError("boom")

app = web.get_application()
app.dependency_overrides[get_readiness_service] = NotReady
app.add_api_route("/infrastructure", infrastructure)
app.add_api_route("/unexpected", unexpected)
client = TestClient(app, raise_server_exceptions=False)
answers = {}
for path in ("/ready/", "/infrastructure", "/unexpected"):
    events.clear()
    status = client.get(path).status_code
    answers[path] = [status, [e["exception"]["values"][-1]["type"] for e in events]]
print(json.dumps(answers))
"""

AIOHTTP_REQUEST = """
import asyncio
import aiohttp
from aiohttp import web
from src.main.sentry import init_sentry

received = []

async def answer(request):
    received.extend(request.headers)
    return web.Response(text="ok")

async def call():
    server = web.Application()
    server.router.add_get("/{path:.*}", answer)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    async with aiohttp.ClientSession() as session:
        await session.get(f"http://127.0.0.1:{port}/bot-s3cr3t/send?token=s3cr3t")
    sentry_sdk.capture_message("after the call")
    await runner.cleanup()

init_sentry()
asyncio.run(call())
print(json.dumps({"events": events, "received": received}))
"""


LATE_FAILURES = """
from collections.abc import AsyncGenerator
import io
import logging
import socket
import threading
import time
from typing import Annotated
from fastapi import BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import DBAPIError
import uvicorn
import src.main.web as web

SECRET = "secret@example.com"

def refuse():
    raise DBAPIError("SELECT 1", None, ValueError(SECRET))

async def teardown():
    yield
    refuse()

async def dependency(_: Annotated[None, Depends(teardown)]):
    return {}

async def background(tasks: BackgroundTasks):
    tasks.add_task(refuse)
    return {}

async def body() -> AsyncGenerator[bytes]:
    yield b"started"
    refuse()

async def stream():
    return StreamingResponse(body())

async def ordinary(tasks: BackgroundTasks):
    tasks.add_task(int, "plain text")
    return {}

async def group(tasks: BackgroundTasks):
    async def fail():
        try:
            refuse()
        except DBAPIError as error:
            raise ExceptionGroup(SECRET, [error, ValueError(SECRET)]) from error
    tasks.add_task(fail)
    return {}

app = web.get_application()
paths = [dependency, background, stream, ordinary, group]
for handler in paths:
    app.add_api_route("/" + handler.__name__, handler)
server_log = logging.getLogger("uvicorn.error")
outcome = {}
for handler in paths:
    events.clear()
    text = io.StringIO()
    server_log.handlers = [logging.StreamHandler(text)]
    server = uvicorn.Server(
        uvicorn.Config(app, port=0, lifespan="off", log_config=None, access_log=False)
    )
    thread = threading.Thread(target=server.run)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    with socket.create_connection(("127.0.0.1", port)) as client:
        client.sendall(
            f"GET /{handler.__name__} HTTP/1.1\\r\\nHost: x\\r\\n"
            "Connection: close\\r\\n\\r\\n".encode()
        )
        answer = b"".join(iter(lambda: client.recv(65536), b""))
    # Leaves once the request task is over, so nothing it logs or sends is missed.
    server.should_exit = True
    thread.join()
    outcome[handler.__name__] = {
        "status": answer.split()[1].decode(),
        "log": text.getvalue(),
        "events": list(events),
    }
print(json.dumps(outcome))
"""

LOGGED_FAILURE = """
import logging
from sqlalchemy.exc import DBAPIError
from src.main.sentry import init_sentry

init_sentry()
logger = logging.getLogger("some.library")
def fail():
    raise DBAPIError("SELECT 1", None, ValueError("secret@example.com"))

try:
    fail()
except DBAPIError as error:
    logger.critical(
        f"failed: {error}", exc_info=True, stack_info=True, extra={"cause": str(error)}
    )
try:
    fail()
except DBAPIError as error:
    logger.critical("failed: %s", error, exc_info=True)
try:
    raise RuntimeError("plain")
except RuntimeError as error:
    logger.critical("failed: %s", error, exc_info=True, extra={"cause": "kept"})
logger.warning("pool: %s", DBAPIError("SELECT 1", None, ValueError("secret@example.com")))
logger.critical(
    "no traceback: %s",
    DBAPIError("SELECT 1", None, ValueError("secret@example.com")),
    extra={"cause": DBAPIError("SELECT 1", None, ValueError("secret@example.com"))},
)
logger.critical("last")
print(json.dumps(events))
"""

HTTPLIB_REQUEST = """
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import urllib.request
from src.main.sentry import init_sentry

received = []

class Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        received.extend(name.lower() for name in self.headers)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass

init_sentry()
server = HTTPServer(("127.0.0.1", 0), Answer)
threading.Thread(target=server.handle_request).start()
urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/").close()
server.server_close()
print(json.dumps(received))
"""


def run(script: str) -> Any:
    environment = {**os.environ, "TESTING": "true"}
    completed = subprocess.run(
        [sys.executable, "-c", PRELUDE + script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def test_the_application_reports_a_fault_once_and_a_probe_never() -> None:
    """Initialized after the app was built, the SDK's integrations patched nothing;
    initialized before it with their defaults, they reported every 5xx on their own,
    a probe's 503 included."""
    assert run(APPLICATION) == {
        "/ready/": [503, []],
        "/infrastructure": [500, ["InfrastructureException"]],
        "/unexpected": [500, ["RuntimeError"]],
    }


def test_a_third_party_call_leaves_no_token_in_an_event() -> None:
    """The breadcrumb the SDK keeps of an aiohttp request holds its full URL."""
    outcome = run(AIOHTTP_REQUEST)

    [event] = outcome["events"]
    assert event["message"] == "after the call"
    assert "s3cr3t" not in json.dumps(event)
    assert not {"sentry-trace", "baggage"} & {h.lower() for h in outcome["received"]}


def test_an_error_raised_after_the_response_started_is_scrubbed() -> None:
    """It is past every middleware: only Sentry's own integration and the server's
    logger see it."""
    outcome = run(LATE_FAILURES)

    for name in ("dependency", "background", "stream", "group"):
        late = outcome[name]
        assert late["status"] == "200", name
        [event] = late["events"]
        [exception] = event["exception"]["values"]
        assert exception["type"] == "ScrubbedError"
        assert "DBAPIError driver=ValueError" in exception["value"]
        assert exception["mechanism"]["handled"] is False
        frames = exception["stacktrace"]["frames"]
        assert any(frame["in_app"] for frame in frames)
        assert not any("vars" in frame for frame in frames)
        assert "secret@example.com" not in json.dumps(event)
        assert "Exception in ASGI application" in late["log"]
        assert "DBAPIError driver=ValueError" in late["log"]
        assert "secret@example.com" not in late["log"]


def test_an_ordinary_error_after_the_response_started_keeps_its_text() -> None:
    late = run(LATE_FAILURES)["ordinary"]

    [event] = late["events"]
    assert event["exception"]["values"][-1]["type"] == "ValueError"
    assert "plain text" in event["exception"]["values"][-1]["value"]
    assert "plain text" in late["log"]


def test_a_logged_error_leaves_no_text_in_the_message_or_the_extra() -> None:
    [interpolated, with_argument, ordinary, untraced, last] = run(LOGGED_FAILURE)

    for event in (interpolated, with_argument, untraced):
        assert "secret@example.com" not in json.dumps(event)
        assert event["logentry"] == {"message": "DBAPIError driver=ValueError"}
        assert event["logger"] == "some.library"
    assert ordinary["logentry"]["formatted"] == "failed: plain"
    assert ordinary["extra"]["cause"] == "kept"
    crumbs = [crumb["message"] for crumb in last["breadcrumbs"]["values"]]
    assert crumbs.count("DBAPIError driver=ValueError") == 4
    assert "failed: plain" in crumbs
    assert "secret@example.com" not in json.dumps(last)


def test_no_trace_header_goes_out_through_a_client_sentry_still_patches() -> None:
    received = run(HTTPLIB_REQUEST)

    assert "host" in received
    assert not {"sentry-trace", "baggage"} & set(received)
