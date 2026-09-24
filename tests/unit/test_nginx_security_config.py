from pathlib import Path
import re

from src.core.errors.codes import ErrorCode
from src.core.middleware import BASE_SECURITY_HEADERS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = PROJECT_ROOT / "infra/nginx"
SERVER_CONFIGS = ("app.conf", "tls.conf.example")


def _read(name: str) -> str:
    return (NGINX_DIR / name).read_text(encoding="utf-8")


def test_nginx_app_config_includes_security_headers_and_body_limit() -> None:
    app_conf = _read("app.conf")

    assert "server_tokens off;" in app_conf
    assert "client_max_body_size 20m;" in app_conf
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
        ).replace("location / {\n        return 301", "").replace(
            "location / {\n        return 444", ""
        )

    proxy_inc = _read("proxy.inc")
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in proxy_inc
    assert "proxy_set_header Connection $connection_upgrade;" in proxy_inc


def _server_blocks(conf: str) -> list[str]:
    """Top-level `server { ... }` bodies, found by brace depth."""
    blocks = []
    for match in re.finditer(r"^server \{", conf, flags=re.MULTILINE):
        depth, index = 0, match.end() - 1
        while True:
            if conf[index] == "{":
                depth += 1
            elif conf[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        blocks.append(conf[match.end() : index])
    return blocks


def test_every_nginx_error_code_is_a_registered_error_code() -> None:
    """The frontend localizes by `code`, and ErrorCode is the registry it is
    built from; a code only nginx emits must still be listed there."""
    codes = set(re.findall(r'"code":"([a-z_]+)"', _read("error_pages.inc")))

    assert codes
    assert codes <= {member.value for member in ErrorCode}


def test_errors_raised_while_parsing_the_request_line_use_uri_error_pages() -> None:
    """A malformed request line (`/%00`) or an overlong URI is refused before
    the request has a URI, and nginx cannot jump to a named location from
    there: it answers its own HTML 500 instead of the JSON page."""
    error_pages = _read("error_pages.inc")

    for status in ("400", "414"):
        directive = re.search(rf"^error_page {status}\b.*;$", error_pages, re.M)
        assert directive is not None, status
        assert "@" not in directive.group(0), directive.group(0)


def test_unknown_hosts_are_dropped_by_a_default_server() -> None:
    """The app answers only for configured names: a forged Host never reaches
    it, and nothing nginx builds (a redirect, a page) reflects that Host."""
    for name in SERVER_CONFIGS:
        blocks = _server_blocks(_read(name))
        defaults = [block for block in blocks if "default_server" in block]
        http_default = next(block for block in defaults if "listen 80 " in block)

        assert "return 444;" in http_default, name
        assert "include /etc/nginx/conf.d/error_pages.inc;" in http_default, name
        for block in blocks:
            if "default_server" not in block:
                assert "server_name _;" not in block, name
                assert "$host" not in block.replace("proxy_set_header Host $host", "")

    tls_default = next(
        block
        for block in _server_blocks(_read("tls.conf.example"))
        if "listen 443 ssl default_server" in block
    )
    assert "ssl_reject_handshake on;" in tls_default


def test_the_api_vhost_refuses_methods_outside_the_api_set() -> None:
    proxy_inc = _read("proxy.inc")

    assert "include /etc/nginx/conf.d/error_pages.inc;" in proxy_inc
    assert (
        "if ($request_method !~ ^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$)"
        in proxy_inc
    )


def test_compose_mounts_every_file_the_configs_include() -> None:
    compose = (PROJECT_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")

    for included in ("proxy.inc", "error_pages.inc"):
        assert f"./nginx/{included}:/etc/nginx/conf.d/{included}:ro" in compose
