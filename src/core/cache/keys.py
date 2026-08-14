def value_key(prefix: str, namespace: str, version: int, suffix: str) -> str:
    return f"{prefix}:{namespace}:v{version}:{suffix}"


def version_key(prefix: str, namespace: str) -> str:
    return f"{prefix}-ver:{namespace}"
