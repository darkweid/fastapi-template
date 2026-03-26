from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_nginx_app_config_includes_security_headers_and_body_limit() -> None:
    app_conf = (PROJECT_ROOT / "infra/nginx/app.conf").read_text(encoding="utf-8")

    assert "server_tokens off;" in app_conf
    assert "client_max_body_size 10m;" in app_conf
    assert 'add_header X-Content-Type-Options "nosniff" always;' in app_conf
    assert 'add_header X-Frame-Options "DENY" always;' in app_conf
    assert (
        "add_header Strict-Transport-Security "
        '"max-age=31536000; includeSubDomains; preload" always;'
    ) in app_conf
    assert "add_header Content-Security-Policy" not in app_conf
    assert (
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;'
    ) in app_conf
    assert (
        "add_header Permissions-Policy "
        '"camera=(), microphone=(), geolocation=()" always;'
    ) in app_conf
