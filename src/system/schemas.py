from typing import Literal

from src.core.schemas import Base


class ProbeResponse(Base):
    """Minimal payload for the liveness and readiness probes."""

    status: Literal["ok"] = "ok"


class HealthCheckResponse(Base):
    """
    Detailed per-dependency report served at /health/ for monitoring.

    Orchestrators must not consult it: a Redis outage degrades the status
    without making the process itself unhealthy.
    """

    status: Literal["ok", "degraded"]
    postgres: bool
    redis: bool


class ServerTimeResponse(Base):
    time: str
