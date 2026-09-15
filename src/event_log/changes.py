from typing import Any

# Matched as substrings, not exact names: an audit row outlives the account it
# describes, and a column called `refresh_token` or `otp_secret` must be left
# out by the same rule that leaves out `password_hash`.
_NEVER_LOGGED_MARKERS = ("password", "token", "secret", "api_key", "apikey")


def _is_secret(field: str) -> bool:
    lowered = field.lower()
    return any(marker in lowered for marker in _NEVER_LOGGED_MARKERS)


def changed_fields(
    instance: object, update_data: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Pair each field's stored value with the one about to replace it.

    Call BEFORE the repository `update()`: it reads the current values off the
    loaded instance, and `update()` overwrites them in place.

    Fields the instance does not carry, fields whose value is unchanged, and
    the secret fields above are left out, so an empty result means the request
    changed nothing.
    """
    changes: dict[str, tuple[Any, Any]] = {}
    for field, new_value in update_data.items():
        if _is_secret(field) or not hasattr(instance, field):
            continue
        old_value = getattr(instance, field)
        if old_value != new_value:
            changes[field] = (old_value, new_value)
    return changes
