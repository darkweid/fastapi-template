from enum import Enum

from fastapi import FastAPI
from fastapi.routing import APIRoute

from loggers import get_logger

logger = get_logger(__name__)


def _is_docs_route(route: APIRoute) -> bool:
    docs_paths = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    if getattr(route, "path", None) in docs_paths:
        return True
    name = getattr(route, "name", "") or ""
    if name.startswith("openapi") or name in {
        "swagger_ui_html",
        "swagger_ui_redirect",
        "redoc_html",
    }:
        return True
    return False


def log_routes_summary(application: FastAPI, include_debug_list: bool = False) -> None:
    routes = [route for route in application.routes if isinstance(route, APIRoute)]
    custom_routes = [route for route in routes if not _is_docs_route(route)]

    total = len(custom_routes)
    by_method: dict[str, int] = {}
    by_tag: dict[str, int] = {}

    for route in custom_routes:
        route_methods: set[str] = route.methods or set()
        for method in route_methods:
            by_method[method] = by_method.get(method, 0) + 1
        tags: list[str] = [
            tag.value if isinstance(tag, Enum) else tag for tag in route.tags or []
        ]
        if not tags:
            by_tag["<untagged>"] = by_tag.get("<untagged>", 0) + 1
        else:
            for tag in tags:
                by_tag[tag] = by_tag.get(tag, 0) + 1

    logger.info(
        "API endpoints summary: total=%s methods=%s tags=%s",
        total,
        by_method,
        by_tag,
    )

    if include_debug_list:
        for route in sorted(
            custom_routes,
            key=lambda item: (min(item.methods) if item.methods else "", item.path),
        ):
            route_methods_repr = (
                ",".join(sorted(route.methods)) if route.methods else ""
            )
            logger.debug(
                "Route: %s %s -> %s", route_methods_repr, route.path, route.name
            )
