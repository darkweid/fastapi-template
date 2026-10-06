import pytest

from src.core.http.user_agent import build_user_agent


@pytest.mark.parametrize(
    ("project_name", "version", "expected"),
    [
        ("App service documentation", "0.0.0", "app-service-documentation/0.0.0"),
        ("SP Beauty API", "0.1.0", "sp-beauty-api/0.1.0"),
        ("!!!", "1", "app/1"),
        ("Shop", "1.0 beta", "shop/1.0-beta"),
    ],
)
def test_the_product_token_comes_from_the_project_name(
    project_name: str, version: str, expected: str
) -> None:
    """A User-Agent product token cannot hold spaces; PROJECT_NAME usually does."""
    assert build_user_agent(project_name, version) == expected
