from src.core.email_service.smtp_mailer import build_template_environment


def test_email_template_does_not_render_template_reference() -> None:
    environment = build_template_environment()

    html = environment.get_template("notification.html").render(
        title="Notification",
        message="Hello",
        logo_url="https://example.com/logo.png",
        year=2026,
    )

    assert "TemplateReference" not in html
