from pathlib import Path
import re
import shlex
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = PROJECT_ROOT / "infra/deploy/deploy.sh"
NGINX_DIR = PROJECT_ROOT / "infra/nginx"
ROLL_WORKERS = '"${COMPOSE[@]}" up -d --no-deps --wait worker scheduler'


def _script() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _compose_service(name: str) -> dict[str, Any]:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    )
    service: dict[str, Any] = compose["services"][name]
    return service


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)s", duration)
    assert match is not None, duration
    return int(match.group(1))


def _gunicorn_option(option: str) -> int:
    argv = shlex.split(_compose_service("app")["command"])
    return int(argv[argv.index(option) + 1])


def test_the_previous_image_is_recorded_before_the_application_rolls() -> None:
    """Recorded after the roll it would name the new image, and a rollback
    would put the broken containers straight back."""
    script = _script()

    recorded = script.index("PREVIOUS_IMAGE=\"$(docker inspect -f '{{.Image}}'")
    assert recorded < script.index('if ! roll_app "$APP_IMAGE"; then')


def test_an_unhealthy_worker_or_scheduler_takes_the_whole_stack_back() -> None:
    """The app is rolled first; left on the new image while the worker and
    the scheduler go back, two versions of the code would run side by side."""
    script = _script()

    assert f"if ! {ROLL_WORKERS}; then" in script
    assert f'APP_IMAGE="$PREVIOUS_IMAGE" {ROLL_WORKERS}' in script
    assert 'roll_app "$PREVIOUS_IMAGE"' in script


def test_the_app_is_scaled_up_beside_the_serving_container() -> None:
    """Recreating the single app container is what left nginx answering 502
    while the new process started."""
    script = _script()

    assert "up -d --no-deps --no-recreate --scale" in script
    assert "--wait app" not in script


def test_nginx_is_reloaded_never_restarted() -> None:
    """A restart drops every connection in flight; a reload lets them finish."""
    script = _script()

    assert "exec -T nginx nginx -s reload" in script
    for path in (DEPLOY_SCRIPT, PROJECT_ROOT / "Makefile"):
        assert "restart nginx" not in path.read_text(encoding="utf-8"), path.name


def test_the_nginx_test_renders_the_upstream_template_first() -> None:
    """With --entrypoint the image's template step is skipped, and nginx -t
    fails on the missing app_upstream.inc instead of testing the config."""
    script = _script()

    assert '"${COMPOSE[@]}" run --rm --no-deps nginx nginx -t' in script
    assert "--entrypoint nginx" not in script


def test_the_app_service_can_run_two_containers() -> None:
    """A fixed name or host port makes the second container of a roll fail to
    start."""
    app = _compose_service("app")

    assert "container_name" not in app
    assert "ports" not in app


def test_docker_waits_longer_than_gunicorn_drains() -> None:
    """Stopped sooner, docker kills the requests the deploy lets drain."""
    stop_grace = _seconds(_compose_service("app")["stop_grace_period"])

    assert stop_grace > _gunicorn_option("--graceful-timeout")


def test_nginx_closes_idle_upstream_connections_before_the_app_does() -> None:
    """The other way round, the app can close a connection while nginx writes a
    request on it, which nginx cannot retry for a POST."""
    main_conf = (NGINX_DIR / "main.conf").read_text(encoding="utf-8")
    match = re.search(r"^\s*keepalive_timeout (\d+)s;$", main_conf, re.M)
    assert match is not None

    assert int(match.group(1)) < _gunicorn_option("--keep-alive")


def test_nginx_routes_to_the_upstream_file_the_deploy_rewrites() -> None:
    """deploy.sh writes the container names into /etc/nginx/app_upstream.inc;
    an upstream declared anywhere else would ignore them."""
    main_conf = (NGINX_DIR / "main.conf").read_text(encoding="utf-8")
    template = (NGINX_DIR / "app_upstream.inc.template").read_text(encoding="utf-8")
    nginx = _compose_service("nginx")

    assert "include /etc/nginx/app_upstream.inc;" in main_conf
    assert "resolver 127.0.0.11" in main_conf
    assert "/etc/nginx/app_upstream.inc" in _script()
    assert template.strip() == "server app:${APP_BACKEND_PORT} resolve;"
    assert nginx["environment"]["NGINX_ENVSUBST_TEMPLATE_DIR"] == "/etc/nginx/conf.d"
    assert nginx["environment"]["NGINX_ENVSUBST_OUTPUT_DIR"] == "/etc/nginx"
    for name in ("app.conf", "tls.conf.example"):
        conf = (NGINX_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*upstream ", conf, re.M), name


def test_a_request_that_reached_the_app_is_never_sent_twice() -> None:
    proxy_inc = (NGINX_DIR / "proxy.inc").read_text(encoding="utf-8")
    match = re.search(r"^proxy_next_upstream ([^;]+);$", proxy_inc, re.M)
    assert match is not None

    assert "non_idempotent" not in match.group(1)


def test_nginx_mounts_its_configuration_as_a_directory() -> None:
    """`git checkout` replaces a changed file with a new inode and a single-file
    bind mount keeps the old one, so the deploy's reload served the previous
    configuration while the nginx -t pre-check passed the new one."""
    volumes = _compose_service("nginx")["volumes"]

    assert "./nginx:/etc/nginx/conf.d:ro" in volumes
    assert not [volume for volume in volumes if volume.startswith("./nginx/")]


def test_every_step_of_the_roll_checks_its_own_status() -> None:
    """roll_app runs as an `if` condition, where errexit is off: an unchecked
    failed reload would let it stop the old container nginx still routes to."""
    script = _script()
    body = script[
        script.index("roll_app() {") : script.index(
            "\n}\n", script.index("roll_app() {")
        )
    ]
    critical = [
        line.strip()
        for line in body.splitlines()
        if re.match(
            r"\s*(route_app_to|docker stop|docker rm|\"\$\{COMPOSE\[@\]\}\")", line
        )
    ]

    assert critical
    for line in critical:
        assert line.endswith(("|| return 1", "|| true")), line


def test_no_pipeline_is_cut_short_by_head() -> None:
    """Under pipefail the writer's SIGPIPE fails the pipeline, and errexit then
    aborts a deploy that finds two healthy app containers."""
    assert "| head" not in _script()
