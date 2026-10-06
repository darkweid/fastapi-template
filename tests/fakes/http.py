from collections.abc import Mapping
from dataclasses import dataclass
import json as jsonlib

from multidict import CIMultiDict, CIMultiDictProxy

from src.core.http.interface import CLIENT_DEFAULT, ClientDefault
from src.core.http.options import HttpTimeout
from src.core.http.response import HttpResponse
from src.core.http.retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    path: str
    operation: str
    params: Mapping[str, str] | None
    json: object
    data: Mapping[str, str] | bytes | None
    headers: Mapping[str, str] | None


def fake_response(
    status: int = 200,
    *,
    json: object = None,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    if body is None:
        body = b"" if json is None else jsonlib.dumps(json).encode()
    return HttpResponse(
        status=status, headers=CIMultiDictProxy(CIMultiDict(headers or {})), body=body
    )


class FakeHttpClient:
    """`HttpRequester` for an integration's tests: records every request and
    answers from a queue of responses or exceptions, repeating the last."""

    def __init__(self, *answers: HttpResponse | Exception) -> None:
        self.answers: list[HttpResponse | Exception] = list(answers) or [
            fake_response()
        ]
        self.requests: list[RecordedRequest] = []
        self.closed = False

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Mapping[str, str] | None = None,
        json: object = None,
        data: Mapping[str, str] | bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: HttpTimeout | None = None,  # noqa: ASYNC109
        retry: RetryPolicy | ClientDefault | None = CLIENT_DEFAULT,
    ) -> HttpResponse:
        self.requests.append(
            RecordedRequest(method, path, operation, params, json, data, headers)
        )
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def aclose(self) -> None:
        self.closed = True
