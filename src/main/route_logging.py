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
    routes = [r for r in application.routes if isinstance(r, APIRoute)]
    custom_routes = [r for r in routes if not _is_docs_route(r)]

    total = len(custom_routes)
    by_method: dict[str, int] = {}
    by_tag: dict[str, int] = {}

    for r in custom_routes:
        methods = getattr(r, "methods", set()) or set()
        for m in methods:
            by_method[m] = by_method.get(m, 0) + 1
        tags = getattr(r, "tags", []) or []
        if not tags:
            by_tag["<untagged>"] = by_tag.get("<untagged>", 0) + 1
        else:
            for t in tags:
                by_tag[t] = by_tag.get(t, 0) + 1

    logger.info(
        "API endpoints summary: total=%s methods=%s tags=%s",
        total,
        by_method,
        by_tag,
    )

    if include_debug_list:
        for r in sorted(
            custom_routes, key=lambda x: (min(x.methods) if x.methods else "", x.path)
        ):
            methods = ",".join(sorted(r.methods)) if r.methods else ""
            logger.debug("Route: %s %s -> %s", methods, r.path, r.name)
