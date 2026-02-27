from fastapi import Request, Response


async def noop_rate_limiter(self, request: Request, response: Response) -> None:
    return None
