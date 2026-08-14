from typing import Literal

from src.core.schemas import Base


class ProbeResponse(Base):
    """Minimal payload for the liveness and readiness probes."""

    status: Literal["ok"] = "ok"


class HealthCheckResponse(Base):
    """
    Detailed per-dependency report served at /health/ for monitoring.

    Always answers 200, including while a dependency is down - that is the one
    moment the per-dependency detail matters. Orchestrators read /live/ and
    /ready/ instead, which turn an outage into a status code.
    """

    status: Literal["ok", "degraded"]
    postgres: bool
    redis: bool


class ServerTimeResponse(Base):
    time: str
