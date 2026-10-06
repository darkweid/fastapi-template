def is_permanent_failure(status: int) -> bool:
    """Retrying cannot help on a 4xx other than a timeout or a rate limit."""
    return 400 <= status < 500 and status not in (408, 429)
