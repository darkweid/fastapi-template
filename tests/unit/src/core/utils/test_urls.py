from src.core.utils.urls import build_public_url


def test_build_public_url_joins_base_and_path() -> None:
    assert (
        build_public_url("https://app.example.com", "/reset-password")
        == "https://app.example.com/reset-password"
    )


def test_build_public_url_collapses_duplicate_slashes() -> None:
    assert (
        build_public_url("https://app.example.com/", "/reset-password")
        == "https://app.example.com/reset-password"
    )


def test_build_public_url_appends_encoded_query() -> None:
    url = build_public_url("https://app.example.com", "/reset-password", token="a b&c")

    assert url == "https://app.example.com/reset-password?token=a+b%26c"
