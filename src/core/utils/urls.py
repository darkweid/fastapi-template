from urllib.parse import urlencode


def build_public_url(base_url: str, path: str, **query: str) -> str:
    """
    Join a configured public base URL with a path and query parameters.

    Kept free of the config singleton so the caller has to name the base URL it
    means; the whole point of PUBLIC_BASE_URL is that no request header ever
    reaches a link that goes out by email.
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url
