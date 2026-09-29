from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = PROJECT_ROOT / "infra/deploy/deploy.sh"
ROLL = '"${COMPOSE[@]}" up -d --no-deps --wait app worker scheduler'


def test_the_previous_image_is_recorded_before_the_application_rolls() -> None:
    """Recorded after the roll it would name the new, unhealthy image, and the
    rollback would put the broken containers straight back."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    recorded = script.index("PREVIOUS_IMAGE=\"$(docker inspect -f '{{.Image}}'")
    assert recorded < script.index(ROLL)


def test_an_unhealthy_roll_falls_back_to_the_previous_image() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert f"if ! {ROLL}; then" in script
    assert f'APP_IMAGE="$PREVIOUS_IMAGE" {ROLL}' in script


def test_nginx_is_reloaded_never_restarted() -> None:
    """A restart drops every connection in flight; a reload resolves the
    recreated app just the same and lets them finish."""
    for path in (DEPLOY_SCRIPT, PROJECT_ROOT / "Makefile"):
        text = path.read_text(encoding="utf-8")
        assert "restart nginx" not in text, path.name
        assert "exec -T nginx nginx -s reload" in text, path.name
