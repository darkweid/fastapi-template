from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response


async def noop_rate_limiter(self, request: Request, response: Response) -> None:
    return None
