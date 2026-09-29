from datetime import datetime
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
    # used_memory / maxmemory of the Redis instance; null when Redis is down or
    # has no memory cap. Above REDIS_MEMORY_DEGRADED_RATIO the status degrades:
    # under `noeviction` a full Redis refuses writes, so logins, OTPs and task
    # enqueues start failing before anything is down.
    redis_memory_used_ratio: float | None = None


class ServerTimeResponse(Base):
    # A datetime, not a preformatted string: the serializer writes UTC as
    # `Z`, the same spelling as every other instant the API answers with.
    time: datetime
