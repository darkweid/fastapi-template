from pathlib import Path

from src.core.middleware import BASE_SECURITY_HEADERS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = PROJECT_ROOT / "infra/nginx"


def _read(name: str) -> str:
    return (NGINX_DIR / name).read_text(encoding="utf-8")


def test_nginx_app_config_includes_security_headers_and_body_limit() -> None:
    app_conf = _read("app.conf")

    assert "server_tokens off;" in app_conf
    assert "client_max_body_size 10m;" in app_conf
    assert 'add_header X-Content-Type-Options "nosniff" always;' in app_conf
    assert 'add_header X-Frame-Options "DENY" always;' in app_conf
    assert "add_header Content-Security-Policy" not in app_conf
    assert (
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;'
    ) in app_conf
    assert (
        "add_header Permissions-Policy "
        '"camera=(), microphone=(), geolocation=()" always;'
    ) in app_conf


def test_no_nginx_config_sets_strict_transport_security() -> None:
    """
    HSTS has exactly one source, the application middleware. A second header with a
    different max-age or preload flag contradicts the first, and the browser resolves
    that by whichever arrived first - so how long clients stay pinned to HTTPS would
    come down to header ordering.
    """
    assert "Strict-Transport-Security" in BASE_SECURITY_HEADERS

    for conf in NGINX_DIR.iterdir():
        assert "add_header Strict-Transport-Security" not in conf.read_text(
            encoding="utf-8"
        ), f"{conf.name} sets HSTS, which the application already sends"


def test_the_plain_http_and_tls_servers_share_one_proxy_body() -> None:
    """
    The proxy body lives in proxy.inc so the two servers cannot drift apart; a
    location block copied into either file is the drift this guards against.
    """
    include_line = "include /etc/nginx/conf.d/proxy.inc;"

    for name in ("app.conf", "tls.conf.example"):
        conf = _read(name)
        assert include_line in conf
        assert "location /" not in conf.replace(
            "location /.well-known/acme-challenge/", ""
        ).replace("location / {\n        return 301", "")

    proxy_inc = _read("proxy.inc")
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in proxy_inc
    assert "proxy_set_header Connection $connection_upgrade;" in proxy_inc
