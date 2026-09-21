from fastapi import routing
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from src.core.limiter.depends import RateLimiter
from src.user.auth.dependencies import (
    get_user_id_from_access_token,
    get_user_id_from_refresh_token,
)
from src.user.auth.realm import USER_AUTH_REALM


def _rate_limiters(dependant: Dependant) -> list[RateLimiter]:
    found = [dependant.call] if isinstance(dependant.call, RateLimiter) else []
    for child in dependant.dependencies:
        found.extend(_rate_limiters(child))
    return found


def _limited_routes(app) -> dict[str, list[RateLimiter]]:
    # `app.routes` nests included routers behind `_IncludedRouter` wrappers, so a
    # mounted path is only visible through the effective route contexts. See the
    # note in tests/unit/src/user/auth/test_token_transport.py: this traversal
    # helper is a FastAPI internal, and an ImportError after an upgrade means the
    # helper needs replacing, not the assertion.
    limited = {}
    for route_context in routing.iter_route_contexts(app.routes):
        if (
            not isinstance(route_context.original_route, APIRoute)
            or route_context.path is None
        ):
            continue
        limiters = _rate_limiters(route_context.original_route.dependant)
        if limiters:
            limited.setdefault(route_context.path, []).extend(limiters)
    return limited


def test_the_refresh_bound_identifier_is_only_used_where_the_cookie_reaches(
    app,
) -> None:
    """The refresh cookie is path-scoped, so this identifier answers 401 on any
    other route - before the endpoint runs, and for every caller."""
    limited = _limited_routes(app)
    assert limited, "No route is rate limited - the walker would pass vacuously"

    misplaced = [
        path
        for path, limiters in limited.items()
        if any(
            limiter.identifier is get_user_id_from_refresh_token for limiter in limiters
        )
        and not path.startswith(USER_AUTH_REALM.refresh_cookie_path)
    ]

    assert (
        not misplaced
    ), f"refresh-bound rate limit outside the cookie's path: {misplaced}"


def test_the_password_change_is_limited_per_user(app) -> None:
    """An authenticated route counts the principal, not the address it came from:
    a household or an office behind one NAT shares a per-IP budget."""
    limited = _limited_routes(app)

    identifiers = {
        limiter.identifier for limiter in limited.get("/v1/users/me/password", [])
    }

    assert identifiers == {get_user_id_from_access_token}
