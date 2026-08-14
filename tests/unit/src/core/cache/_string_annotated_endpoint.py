from __future__ import annotations

from fastapi import Request, Response

from src.core.schemas import Base


class StringAnnotatedSummary(Base):
    name: str


# Every annotation below is a forward-reference string at runtime, exactly as
# `from __future__ import annotations` produces anywhere in src/ - this module
# exists only so cached_route's parameter lookup can be exercised against that.
async def read_user(
    user_id: str, request: Request, response: Response
) -> StringAnnotatedSummary:
    return StringAnnotatedSummary(name=f"user-{user_id}")
