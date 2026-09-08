from src.core.auth.redis_keys import AuthRedisKeyBuilder


def test_every_key_carries_the_realm_prefix() -> None:
    keys = AuthRedisKeyBuilder("staff")

    assert keys.access("42", "s1") == "staff:access:42:s1"
    assert keys.refresh("42", "s1") == "staff:refresh:42:s1"
    assert keys.used("42", "j1") == "staff:used:42:j1"
    assert keys.sessions("42") == "staff:sessions:42"
    assert keys.session_key("access_token", "42", "s1") == "staff:access:42:s1"
    assert keys.session_key("refresh_token", "42", "s1") == "staff:refresh:42:s1"


def test_identifier_bearing_keys_hash_the_identifier() -> None:
    keys = AuthRedisKeyBuilder("staff")

    login_key = keys.login_failures("person@example.com")
    one_time_key = keys.one_time("verification", "person@example.com")

    assert login_key.startswith("staff:login-fail:")
    assert one_time_key.startswith("staff:one-time:verification:")
    assert "person@example.com" not in login_key
    assert "person@example.com" not in one_time_key


def test_two_realms_never_share_a_key() -> None:
    first = AuthRedisKeyBuilder("user")
    second = AuthRedisKeyBuilder("staff")

    assert first.sessions("42") != second.sessions("42")
    assert first.refresh("42", "s1") != second.refresh("42", "s1")
