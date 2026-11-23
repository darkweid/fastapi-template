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
