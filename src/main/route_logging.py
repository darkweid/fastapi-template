from typing import Any

from fastapi import FastAPI

from loggers import get_logger

logger = get_logger(__name__)


def log_routes_summary(application: FastAPI, include_debug_list: bool = False) -> None:
    """Log a human-readable summary of the application's API endpoints.

    The summary is derived from the generated OpenAPI schema, which is the
    public source of truth for registered operations. This keeps the logic
    independent of Starlette's internal routing structures (e.g. the nested
    ``_IncludedRouter`` wrappers produced by ``include_router``).
    """
    schema: dict[str, Any] = application.openapi()
    paths: dict[str, dict[str, Any]] = schema.get("paths", {})

    total = 0
    by_method: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    operations: list[tuple[str, str, str]] = []

    for path, path_item in paths.items():
        for method, operation in path_item.items():
            http_method = method.upper()
            total += 1
            by_method[http_method] = by_method.get(http_method, 0) + 1
            tags: list[str] = operation.get("tags") or ["<untagged>"]
            for tag in tags:
                by_tag[tag] = by_tag.get(tag, 0) + 1
            operations.append((http_method, path, operation.get("operationId", "")))

    logger.info(
        "API endpoints summary: total=%s methods=%s tags=%s",
        total,
        by_method,
        by_tag,
    )

    if include_debug_list:
        for http_method, path, operation_id in sorted(operations):
            logger.debug("Route: %s %s -> %s", http_method, path, operation_id)
