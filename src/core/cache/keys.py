def value_key(prefix: str, namespace: str, version: str, suffix: str) -> str:
    return f"{prefix}:{namespace}:v{version}:{suffix}"


def version_key(prefix: str, namespace: str) -> str:
    return f"{prefix}-ver:{namespace}"


def tag_version_key(prefix: str, tag: str) -> str:
    return f"{prefix}-tag:{tag}"
