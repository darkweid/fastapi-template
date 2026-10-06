from dataclasses import dataclass, fields

import aiohttp


@dataclass(frozen=True, slots=True)
class HttpTimeout:
    """Seconds per phase. Waiting for a free pooled connection counts only
    against `total_seconds`."""

    connect_seconds: float = 5
    read_seconds: float = 10
    total_seconds: float = 30

    def __post_init__(self) -> None:
        if min(self.connect_seconds, self.read_seconds, self.total_seconds) <= 0:
            raise ValueError("HTTP timeouts must be positive")

    def to_aiohttp(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=self.total_seconds,
            sock_connect=self.connect_seconds,
            sock_read=self.read_seconds,
        )


@dataclass(frozen=True, slots=True)
class HttpLimits:
    connections: int = 100
    connections_per_host: int = 20
    keepalive_seconds: float = 30
    dns_cache_seconds: int = 60
    max_response_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(getattr(self, field.name) <= 0 for field in fields(self)):
            raise ValueError("HTTP limits must be positive")


DEFAULT_TIMEOUT = HttpTimeout()
DEFAULT_LIMITS = HttpLimits()
