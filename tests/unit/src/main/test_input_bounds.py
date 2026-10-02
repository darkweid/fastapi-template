from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

JsonSchema = dict[str, Any]
Check = Callable[[JsonSchema], bool]

# A string pydantic parses into something of fixed size. `email` is bounded
# by the validator behind `EmailStr` (254 characters), which publishes no
# `maxLength` of its own. An uploaded file (`contentMediaType`) is bounded by
# the upload limits, an `enum` or `const` by its values.
BOUNDED_STRING_FORMATS = frozenset({"uuid", "date", "date-time", "time", "email"})


def _unbounded(
    schema: JsonSchema,
    location: str,
    components: dict[str, JsonSchema],
    seen: frozenset[str],
    is_unbounded: Check,
) -> list[str]:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return []
        return _unbounded(
            components[name], location, components, seen | {name}, is_unbounded
        )
    found = []
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, []):
            found += _unbounded(branch, location, components, seen, is_unbounded)
    if is_unbounded(schema):
        found.append(location)
    for name, field in schema.get("properties", {}).items():
        found += _unbounded(field, f"{location}.{name}", components, seen, is_unbounded)
    if "items" in schema:
        found += _unbounded(
            schema["items"], f"{location}[]", components, seen, is_unbounded
        )
    return found


def _unbounded_inputs(app: FastAPI, is_unbounded: Check) -> list[str]:
    spec = app.openapi()
    components = spec["components"]["schemas"]
    found = []
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            prefix = f"{method.upper()} {path}"
            for parameter in operation.get("parameters", []):
                location = f"{prefix} {parameter['in']}:{parameter['name']}"
                found += _unbounded(
                    parameter.get("schema", {}),
                    location,
                    components,
                    frozenset(),
                    is_unbounded,
                )
            for content in operation.get("requestBody", {}).get("content", {}).values():
                found += _unbounded(
                    content.get("schema", {}),
                    f"{prefix} body",
                    components,
                    frozenset(),
                    is_unbounded,
                )
    return found


def _is_unbounded_integer(schema: JsonSchema) -> bool:
    return (
        schema.get("type") == "integer"
        and "maximum" not in schema
        and "exclusiveMaximum" not in schema
    )


def _is_unbounded_string(schema: JsonSchema) -> bool:
    return (
        schema.get("type") == "string"
        and "maxLength" not in schema
        and "enum" not in schema
        and "const" not in schema
        and "contentMediaType" not in schema
        and schema.get("format") not in BOUNDED_STRING_FORMATS
    )


def test_every_integer_input_has_an_upper_bound(app: FastAPI) -> None:
    """An integer past PostgreSQL's integer or bigint range reaches asyncpg,
    which refuses to encode it, and the request answers 500 instead of 422.
    Every integer a client sends - body, query, path, header - carries a
    `maximum` (`INT32_MAX` / `INT64_MAX` in `src/core/schemas.py` at most)."""
    assert _unbounded_inputs(app, _is_unbounded_integer) == []


def test_every_string_input_has_a_max_length(app: FastAPI) -> None:
    """A `Text` column stores whatever a request carries, so a string without
    a schema bound is limited only by the request body. Every string a client sends
    - body, query, path, header - carries a `maxLength` chosen by the domain
    and never above its column's `String(n)`."""
    assert _unbounded_inputs(app, _is_unbounded_string) == []
