import re

_NOT_PRODUCT = re.compile(r"[^a-z0-9]+")
_NOT_VERSION = re.compile(r"[^A-Za-z0-9.+_-]+")


def build_user_agent(project_name: str, version: str) -> str:
    """`<product>/<version>` from the project's name. A WAF in front of a
    provider often refuses the library's default `Python/x aiohttp/x`, and a
    product token cannot hold the spaces a display name carries."""
    product = _NOT_PRODUCT.sub("-", project_name.lower()).strip("-") or "app"
    release = _NOT_VERSION.sub("-", version.strip()).strip("-") or "0"
    return f"{product}/{release}"
