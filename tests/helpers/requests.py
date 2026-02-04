from __future__ import annotations

from typing import Any

from starlette.requests import Request


def build_request(
    *,
    path: str = "/",
    method: str = "GET",
    headers: dict[str, str] | None = None,
    query_string: str = "",
    path_params: dict[str, Any] | None = None,
    endpoint: Any | None = None,
) -> Request:
    headers = headers or {}
    encoded_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": encoded_headers,
        "query_string": query_string.encode(),
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "http_version": "1.1",
        "app": None,
        "path_params": path_params or {},
        "endpoint": endpoint,
    }
    return Request(scope)
