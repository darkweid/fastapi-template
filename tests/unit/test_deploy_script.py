import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = PROJECT_ROOT / "infra/deploy/deploy.sh"
NGINX_DIR = PROJECT_ROOT / "infra/nginx"
ROLL_WORKERS = '"${COMPOSE[@]}" up -d --no-deps --wait worker scheduler'
NGINX_TEST = '"${COMPOSE[@]}" run --rm --no-deps nginx nginx -t'


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


def test_the_nginx_test_goes_through_the_service_entrypoint() -> None:
    """With --entrypoint the step writing app_upstream.inc is skipped, and
    nginx -t fails on the missing file instead of testing the config."""
    script = _script()

    assert NGINX_TEST in script
    assert "--entrypoint" not in script


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
    """deploy.sh writes the container names into /etc/nginx/upstream/app_upstream.inc;
    an upstream declared anywhere else would ignore them."""
    main_conf = (NGINX_DIR / "main.conf").read_text(encoding="utf-8")
    entrypoint = (NGINX_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    nginx = _compose_service("nginx")

    assert "include /etc/nginx/upstream/app_upstream.inc;" in main_conf
    assert "resolver 127.0.0.11" in main_conf
    assert "/etc/nginx/upstream/app_upstream.inc" in _script()
    assert 'upstream="$state/app_upstream.inc"' in entrypoint
    assert "server app:%s resolve;" in entrypoint
    assert nginx["entrypoint"] == ["sh", "/etc/nginx/conf.d/entrypoint.sh"]
    assert nginx["command"] == ["nginx", "-g", "daemon off;"]
    for name in ("app.conf", "tls.conf.example"):
        conf = (NGINX_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*upstream ", conf, re.M), name


def test_a_restarted_nginx_keeps_the_pin_it_was_routing_by() -> None:
    """Back on the service name after a restart mid-roll, nginx would route to
    a replica still booting or one already retired."""
    entrypoint = (NGINX_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    script = _script()

    assert 'cp "$applied" "$upstream"' in entrypoint
    assert entrypoint.rstrip().endswith('exec /docker-entrypoint.sh "$@"')
    # The pin is recorded as applied only after the reload that applies it.
    reload = script.index('"${COMPOSE[@]}" exec -T nginx nginx -s reload')
    assert reload < script.index("/etc/nginx/upstream/app_upstream.applied.next")
    assert "cat /etc/nginx/upstream/app_upstream.applied" in script


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


def test_nginx_is_brought_up_to_date_before_the_app_rolls() -> None:
    """An nginx still on an older configuration ignores app_upstream.inc and
    keeps routing to the container the roll is stopping."""
    script = _script()

    assert script.index('"${COMPOSE[@]}" up -d --no-deps nginx') < script.index(
        'if ! roll_app "$APP_IMAGE"; then'
    )


def test_an_interrupted_roll_keeps_the_container_nginx_is_pinned_to() -> None:
    """After the cutover nginx is pinned to the new container; routing to every
    healthy one would bring the retired version back."""
    script = _script()

    assert 'old_ids="$(serving_app_ids)"' in script
    assert 'RUNNING_APP="$(serving_app_ids)"' in script


def test_routing_fails_when_nginx_is_not_running() -> None:
    """nginx is started before the roll; skipping the route when it is down let
    the roll remove the old app and left a pin naming the removed container."""
    script = _script()
    body = script[script.index("route_app_to() {") :]
    body = body[: body.index("\n}\n")]

    assert "return 0" not in body
    assert "if ! nginx_is_running; then" in body


def test_leftover_containers_stop_after_the_retired_nginx_workers() -> None:
    """A container nginx was moved off by an interrupted roll can still carry
    requests from the workers that reload retired."""
    script = _script()
    body = script[script.index("roll_app() {") :]
    leftover = body[body.index('if [ -n "$leftover_ids" ]; then') :]

    assert leftover.index("wait_for_retired_nginx_workers") < leftover.index(
        'docker stop "$id"'
    )
    # The pin reload comes first: the applied record may lag what nginx routes to.
    assert body.index("route_app_to $(container_names $old_ids)") < body.index(
        'if [ -n "$leftover_ids" ]; then'
    )
    assert 'docker rm -f "$id"' not in body[: body.index("discard_new_app()")]


def test_a_failed_roll_moves_nginx_back_before_removing_the_new_container() -> None:
    """A failure after the cutover reload leaves nginx routing to the new
    container; removing it first fails the requests in between."""
    script = _script()
    body = script[script.index("discard_new_app() {") :]
    body = body[: body.index("\n}\n")]

    assert body.index("route_app_to $(container_names $keep)") < body.index(
        "wait_for_retired_nginx_workers"
    )
    assert body.index("wait_for_retired_nginx_workers") < body.index(
        'docker stop "$id"'
    )


def test_generated_upstream_files_stay_outside_the_read_only_mount() -> None:
    """conf.d is mounted read-only; a file written there fails every start."""
    entrypoint = (NGINX_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    written = re.findall(r"/etc/nginx/[\w./]+", entrypoint + _script())

    assert "./nginx:/etc/nginx/conf.d:ro" in _compose_service("nginx")["volumes"]
    assert written
    assert not [path for path in written if path.startswith("/etc/nginx/conf.d/")]


def test_the_applied_pin_survives_a_recreated_nginx() -> None:
    """The deploy recreates nginx whenever its service changes; a pin kept in
    the container layer would fall back to the service name mid-roll."""
    nginx = _compose_service("nginx")
    entrypoint = (NGINX_DIR / "entrypoint.sh").read_text(encoding="utf-8")

    assert "nginx-upstream:/etc/nginx/upstream" in nginx["volumes"]
    assert "state=/etc/nginx/upstream" in entrypoint
    # A pin naming containers that no longer exist is dropped, not served.
    assert 'getent hosts "$host"' in entrypoint


def test_postgres_reads_the_shipped_config_at_every_start() -> None:
    """The config used to be copied into PGDATA by an initdb script, so a
    changed setting never reached a database that already existed, while the
    deploy rebuilt the image as if it would."""
    dockerfile = (PROJECT_ROOT / "infra/postgres/Dockerfile").read_text(
        encoding="utf-8"
    )
    volumes = _compose_service("postgres")["volumes"]

    assert (
        'CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]'
        in dockerfile
    )
    assert "docker-entrypoint-initdb.d" not in dockerfile
    assert not [volume for volume in volumes if "postgresql.conf" in volume]
    assert '"${COMPOSE[@]}" build --pull postgres' in _script()


def _profile_loading() -> str:
    """The part of deploy.sh that reads RESOURCE_PROFILE, runnable on its own."""
    script = _script()
    compose = script[script.index("COMPOSE=(") :]
    compose = compose[: compose.index("\n\n")]
    start = script.index("python3 scripts/ops/check_env.py\n") + len(
        "python3 scripts/ops/check_env.py\n"
    )
    loading = script[start : script.index("# BACKUP_ENABLED switches")]
    return (
        f"set -euo pipefail\n{compose}\n{loading}\n" 'printf "%s\\n" "${COMPOSE[@]}"\n'
    )


def _write_profile(
    root: Path, name: str, sizing: str = "max_connections = 100\n"
) -> None:
    (root / "infra/resources" / name / "postgres").mkdir(parents=True)
    (root / "infra/resources" / name / "compose.yml").write_text("services: {}\n")
    (root / "infra/resources" / name / "postgres/sizing.conf").write_text(sizing)


def _load_profile(root: Path, env_line: str) -> subprocess.CompletedProcess[str]:
    (root / ".env").write_text(f"DEBUG=false\n{env_line}\n")
    # deploy.sh calls the script by its path in the checkout.
    (root / "scripts/ops").mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT_ROOT / "scripts/ops/check_env.py", root / "scripts/ops")
    return subprocess.run(  # noqa: S603 # nosec B603
        ["bash", "-c", _profile_loading()],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            # check_env.py runs on the interpreter running this suite.
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(PROJECT_ROOT),
        },
    )


@pytest.mark.parametrize(
    ("env_line", "refusal"),
    [
        ("RESOURCE_PROFILE=", "is not a profile name"),
        ("RESOURCE_PROFILE= # set me", "is not a profile name"),
        ("RESOURCE_PROFILE=../stage", "is not a profile name"),
        ("RESOURCE_PROFILE=Stage", "is not a profile name"),
        (
            "RESOURCE_PROFILE=missing",
            "infra/resources/missing/compose.yml does not exist",
        ),
        ("RESOURCE_PROFILE=partial", "partial/postgres/sizing.conf does not exist"),
    ],
)
def test_a_deploy_refuses_a_profile_that_sizes_nothing(
    tmp_path: Path, env_line: str, refusal: str
) -> None:
    """Without a profile the box would run with the local default sizing, and a
    path in the name would load an overlay from anywhere on the box."""
    (tmp_path / "infra/resources/partial").mkdir(parents=True)
    (tmp_path / "infra/resources/partial/compose.yml").write_text("services: {}\n")

    result = _load_profile(tmp_path, env_line)

    assert result.returncode == 1
    assert refusal in result.stdout


@pytest.mark.parametrize(
    "env_line", ["RESOURCE_PROFILE=stage", 'RESOURCE_PROFILE="stage"  # 4 CPU / 8 GB']
)
def test_a_deploy_loads_the_overlay_its_profile_names(
    tmp_path: Path, env_line: str
) -> None:
    _write_profile(tmp_path, "stage")

    result = _load_profile(tmp_path, env_line)

    assert result.returncode == 0, result.stdout + result.stderr
    # The status line first.
    assert result.stdout.splitlines()[1:] == [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "infra/docker-compose.yml",
        "-f",
        "infra/resources/stage/compose.yml",
    ]


def test_the_profile_is_read_after_the_gate_refused_an_empty_one() -> None:
    """The name is read the way compose reads .env, by the gate's own parser,
    and only once the gate has refused an empty value with its own message."""
    script = _script()

    assert script.index("python3 scripts/ops/check_env.py\n") < script.index(
        '"$(python3 scripts/ops/check_env.py --get RESOURCE_PROFILE)"'
    )
    assert "python3 -c" not in script


def test_every_compose_call_sees_the_profile_overlay() -> None:
    """A call made before the overlay is appended would create containers with
    the local default sizing."""
    script = _script()
    appended = script.index('COMPOSE+=(-f "${RESOURCE_PROFILE_DIR}/compose.yml")')

    assert appended < script.index(NGINX_TEST)
    assert appended < script.index('RUNNING_APP="$(serving_app_ids)"')
    top_level_calls = [
        match.start() for match in re.finditer(r'^"\$\{COMPOSE\[@\]\}"', script, re.M)
    ]
    assert top_level_calls
    assert appended < min(top_level_calls)


def test_the_app_takes_its_worker_count_from_the_environment() -> None:
    """A -w on the command line wins over WEB_CONCURRENCY, so a profile could
    never size the app."""
    argv = shlex.split(_compose_service("app")["command"])

    assert "-w" not in argv
    assert "--workers" not in argv
    assert not [arg for arg in argv if arg.startswith("--workers=")]
    assert _compose_service("app")["environment"]["WEB_CONCURRENCY"].isdigit()


BACKUP_STANZA_CREATE = "pgbackrest --stanza=main stanza-create"
BACKUP_MARKER_TOUCH = "touch /var/lib/postgresql/pgbackrest/state/stanza-ready"
BACKUP_CHECK = "pgbackrest --stanza=main check"
BACKUP_SIDECAR_UP = '"${COMPOSE[@]}" up -d --no-deps backup'


def test_backups_are_checked_after_postgres_starts_and_before_migrations() -> None:
    script = _script()

    assert (
        script.index('"${COMPOSE[@]}" up -d --wait postgres redis')
        < script.index(BACKUP_STANZA_CREATE)
        < script.index(BACKUP_CHECK)
        < script.index(BACKUP_SIDECAR_UP)
        < script.index("alembic upgrade head")
    )


def test_the_backup_steps_are_bounded() -> None:
    """The outer timeout kills only the compose CLI; pgBackRest keeps running in
    the container unless the inner timeout bounds it too."""
    lines = _script().splitlines()

    for step in (BACKUP_STANZA_CREATE, BACKUP_CHECK):
        (line,) = (line for line in lines if step in line and "exec" in line)
        command = line.strip().replace("if ! ", "")
        assert command.startswith("timeout 300 ")
        assert "postgres postgres timeout 280 pgbackrest" in command


def test_pgbackrest_runs_as_postgres() -> None:
    lines = [
        line
        for line in _script().splitlines()
        if "pgbackrest " in line and "exec" in line
    ]

    assert lines
    assert all("exec -T -u postgres postgres" in line for line in lines)


def test_a_disabled_box_removes_the_sidecar() -> None:
    script = _script()
    removal = '"${COMPOSE[@]}" --profile backup rm -sf backup'

    assert (
        script.index('if [ "$BACKUP_ENABLED" = "true" ]; then\n  # A stanza')
        < script.index(BACKUP_SIDECAR_UP)
        < script.index("\nelse\n  # Removes a sidecar")
        < script.index(removal)
        < script.index("alembic upgrade head")
    )


def test_backup_enabled_is_read_through_the_gate() -> None:
    script = _script()
    read = (
        'BACKUP_ENABLED="$(python3 scripts/ops/check_env.py --enabled BACKUP_ENABLED)"'
    )

    assert script.index("python3 scripts/ops/check_env.py\n") < script.index(read)
    assert (
        script.index(read)
        < script.index("COMPOSE+=(--profile backup)")
        < script.index("run --rm --no-deps nginx")
    )


def test_the_archive_marker_is_written_between_stanza_create_and_check() -> None:
    """The archive wrapper ships WAL only once the marker exists, and check
    forces a WAL switch that needs it shipped."""
    script = _script()

    assert (
        script.index(BACKUP_STANZA_CREATE)
        < script.index(BACKUP_MARKER_TOUCH)
        < script.index(BACKUP_CHECK)
    )


def test_the_pgbackrest_directories_exist_before_the_stanza_is_created() -> None:
    """A data volume that predates the image never gets the log directory, and
    pgBackRest then turns file logging off."""
    script = _script()

    assert (
        script.index("exec -T -u postgres postgres mkdir -p")
        < script.index("pgbackrest/log")
        < script.index(BACKUP_STANZA_CREATE)
    )
    assert "pgbackrest/spool" in script and "pgbackrest/state" in script
