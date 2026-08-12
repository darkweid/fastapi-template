from src.user.auth.csrf import build_csrf_token, verify_csrf_token

SECRET = "unit-test-csrf-secret"
REFRESH_TOKEN = "header.payload.signature"


def test_signature_is_deterministic() -> None:
    assert build_csrf_token(REFRESH_TOKEN, SECRET) == build_csrf_token(
        REFRESH_TOKEN, SECRET
    )


def test_different_refresh_tokens_produce_different_signatures() -> None:
    assert build_csrf_token(REFRESH_TOKEN, SECRET) != build_csrf_token(
        "other.refresh.token", SECRET
    )


def test_different_secrets_produce_different_signatures() -> None:
    assert build_csrf_token(REFRESH_TOKEN, SECRET) != build_csrf_token(
        REFRESH_TOKEN, "another-secret"
    )


def test_verify_accepts_a_matching_token() -> None:
    token = build_csrf_token(REFRESH_TOKEN, SECRET)

    assert verify_csrf_token(REFRESH_TOKEN, token, SECRET) is True


def test_verify_rejects_a_tampered_token() -> None:
    token = build_csrf_token(REFRESH_TOKEN, SECRET)
    tampered = ("b" if token[0] == "a" else "a") + token[1:]

    assert verify_csrf_token(REFRESH_TOKEN, tampered, SECRET) is False


def test_verify_rejects_a_token_signed_for_another_refresh_token() -> None:
    token = build_csrf_token("other.refresh.token", SECRET)

    assert verify_csrf_token(REFRESH_TOKEN, token, SECRET) is False


def test_verify_rejects_missing_values() -> None:
    assert verify_csrf_token(REFRESH_TOKEN, None, SECRET) is False
    assert verify_csrf_token(REFRESH_TOKEN, "", SECRET) is False
