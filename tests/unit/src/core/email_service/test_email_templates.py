from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path("src/core/email_service/templates")


def build_template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )


def test_email_template_does_not_render_template_reference() -> None:
    environment = build_template_environment()

    html = environment.get_template("notification.html").render(
        title="Notification",
        message="Hello",
        logo_url="https://example.com/logo.png",
        year=2026,
    )

    assert "TemplateReference" not in html
