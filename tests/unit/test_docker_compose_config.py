from ipaddress import ip_address, ip_network
import json
from pathlib import Path
import re
import shlex
from typing import Any

import yaml

from scripts.ops.check_env import parse_env
from src.main.config import AppConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict[str, Any]:
    text = (PROJECT_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def _app_network_ipam() -> dict[str, str]:
    configs = _compose()["networks"]["app-network"]["ipam"]["config"]
    assert len(configs) == 1
    ipam: dict[str, str] = configs[0]
    return ipam


def test_app_network_hands_out_addresses_outside_its_gateway() -> None:
    """Docker forwards the connections it proxies to a published port from the
    gateway, every IPv6 client included; the range containers draw from must
    not hold it, or trusting nginx's range would trust those clients too."""
    ipam = _app_network_ipam()
    subnet = ip_network(ipam["subnet"])
    address_range = ip_network(ipam["ip_range"])
    gateway = ip_address(ipam["gateway"])

    assert address_range.subnet_of(subnet)  # type: ignore[arg-type]
    assert gateway in subnet
    assert gateway not in address_range


def test_trust_proxy_hosts_default_is_loopback_and_the_app_network_range() -> None:
    """A wider default (every private network) held the gateway, so any IPv6
    client could forge X-Forwarded-For and pick its own rate-limit bucket."""
    default = AppConfig.model_fields["TRUST_PROXY_HOSTS"].default

    assert default == ["127.0.0.1", "::1", _app_network_ipam()["ip_range"]]


def test_env_example_trusts_the_same_proxy_hosts_as_the_default() -> None:
    example = parse_env(PROJECT_ROOT / ".env.example")

    assert (
        json.loads(example["TRUST_PROXY_HOSTS"])
        == AppConfig.model_fields["TRUST_PROXY_HOSTS"].default
    )


def test_redis_gets_its_own_password_and_no_other_secret() -> None:
    """The app's env_file carries every secret of the stack - JWT keys, SMTP,
    S3 - and Redis needs one of them. A process holding the rest is one more
    place for them to leak from."""
    redis = _compose()["services"]["redis"]

    assert "env_file" not in redis
    assert redis["environment"] == {"REDISCLI_AUTH": "${REDIS_PASSWORD}"}
    assert " -a " not in " ".join(redis["healthcheck"]["test"])


RESOURCES_DIR = PROJECT_ROOT / "infra/resources"
POSTGRES_CONF = PROJECT_ROOT / "infra/postgres/postgresql.conf"
PROFILE_CONF_DIR = "/etc/postgresql/conf.d"
SIZED_SERVICES = {"postgres", "app", "worker", "scheduler", "nginx", "redis", "backup"}


def _profiles() -> list[Path]:
    return sorted(path for path in RESOURCES_DIR.iterdir() if path.is_dir())


def _profile_compose(profile: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        (profile / "compose.yml").read_text(encoding="utf-8")
    )
    return loaded


def _settings(path: Path) -> dict[str, str]:
    settings = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and "=" in line:
            key, _, value = line.partition("=")
            settings[key.strip()] = value.strip()
    return settings


def test_the_shipped_profiles_are_complete() -> None:
    """deploy.sh refuses a profile missing either file, so a half-written one
    fails a deploy instead of a test."""
    assert {profile.name for profile in _profiles()} >= {"stage", "production"}
    for profile in _profiles():
        assert (profile / "compose.yml").is_file(), profile.name
        assert (profile / "postgres/sizing.conf").is_file(), profile.name


def test_a_profile_sizes_only_services_the_base_file_declares() -> None:
    """An overlay naming a service the base lacks creates a half-defined one;
    a misspelled name leaves the real service on the local default."""
    base_services = set(_compose()["services"])
    for profile in _profiles():
        services = _profile_compose(profile)["services"]

        assert set(services) <= base_services, profile.name
        assert set(services) == SIZED_SERVICES, profile.name
        for name, service in services.items():
            limits = service["deploy"]["resources"]["limits"]
            assert {"cpus", "memory"} <= set(limits), f"{profile.name}/{name}"
        assert services["app"]["environment"]["WEB_CONCURRENCY"].isdigit()
        assert "shm_size" in services["postgres"]


def test_a_profile_builds_its_own_sizing_into_postgres() -> None:
    """The sizing is baked into the image, so a changed file is a new image
    `up` recreates the server on; a bind mount would leave it running on the
    old values. An overlay naming another profile would size the box wrongly."""
    dockerfile = (PROJECT_ROOT / "infra/postgres/Dockerfile").read_text(
        encoding="utf-8"
    )
    for profile in _profiles():
        postgres = _profile_compose(profile)["services"]["postgres"]

        assert postgres["build"]["args"]["RESOURCE_PROFILE"] == profile.name
        assert "volumes" not in postgres, profile.name

    assert "ARG RESOURCE_PROFILE=" in dockerfile
    # Only the selected profile's postgres/ joins the build cache key: a wider
    # mount would recreate the server on an edit to any profile or compose.yml.
    assert (
        "source=infra/${RESOURCE_PROFILE:+resources/$RESOURCE_PROFILE/}postgres,"
        in dockerfile
    )
    assert "install -m 0644 /tmp/profile/*.conf /etc/postgresql/conf.d/" in dockerfile


def test_a_profile_keeps_the_base_redis_command() -> None:
    """The overlay replaces the command whole: dropping --requirepass would
    leave Redis open on the network."""
    base_command = _compose()["services"]["redis"]["command"]
    for profile in _profiles():
        command = _profile_compose(profile)["services"]["redis"]["command"]

        assert re.fullmatch(
            re.escape(base_command) + r" --maxmemory \d+[kmg]b", command
        ), profile.name


def _worker_processes() -> int:
    argv = shlex.split(_compose()["services"]["worker"]["command"])
    return int(argv[argv.index("--workers") + 1])


def test_the_worker_pins_its_process_count() -> None:
    """Every worker process holds its own pools, so max_connections is sized
    for a count taskiq's default could change under it."""
    assert _worker_processes() >= 1


def test_a_profile_has_connections_for_a_rolling_deploy() -> None:
    """During a roll two app containers hold their pools at once beside the
    worker and the scheduler; past max_connections the new container fails its
    first queries."""
    example = parse_env(PROJECT_ROOT / ".env.example")
    api_pool = int(example["DB_POOL_SIZE"]) + int(example["DB_MAX_OVERFLOW"])
    tasks_pool = int(example["DB_TASKS_POOL_SIZE"]) + int(
        example["DB_TASKS_MAX_OVERFLOW"]
    )
    worker_processes = _worker_processes()
    for profile in _profiles():
        web_workers = int(
            _profile_compose(profile)["services"]["app"]["environment"][
                "WEB_CONCURRENCY"
            ]
        )
        sizing = _settings(profile / "postgres/sizing.conf")
        app_containers = 2 * web_workers * api_pool
        worker = worker_processes * (tasks_pool + api_pool)
        scheduler = tasks_pool + api_pool
        migration, psql = 1, 1
        needed = app_containers + worker + scheduler + migration + psql

        assert int(sizing["max_connections"]) >= needed, profile.name


def test_postgres_reads_the_profile_last() -> None:
    """A later setting wins, so an include ahead of the base sizing would let
    the base values override the profile."""
    directives = [
        line.strip()
        for line in POSTGRES_CONF.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    dockerfile = (PROJECT_ROOT / "infra/postgres/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert directives[-1] == f"include_dir '{PROFILE_CONF_DIR}'"
    # The server refuses to start on an include_dir that does not exist.
    assert f"mkdir -p {PROFILE_CONF_DIR}" in dockerfile


BACKUP_ENV_FILE = "../.env.backup"


def _env_file_paths(service: dict[str, Any]) -> list[str]:
    entries = service.get("env_file", [])
    if isinstance(entries, str):
        entries = [entries]
    return [entry if isinstance(entry, str) else entry["path"] for entry in entries]


def test_only_postgres_and_backup_load_the_backup_env() -> None:
    """The repository keys and the cipher passphrase must not reach the app,
    worker or scheduler, which run user-facing code."""
    loaders = {
        name
        for name, service in _compose()["services"].items()
        if BACKUP_ENV_FILE in _env_file_paths(service)
    }

    assert loaders == {"postgres", "backup"}


def test_the_backup_env_is_optional() -> None:
    """A box with backups off has no .env.backup; a required file would stop
    compose from starting the stack at all."""
    for name in ("postgres", "backup"):
        entries = _compose()["services"][name]["env_file"]
        entry = next(e for e in entries if e["path"] == BACKUP_ENV_FILE)

        assert entry["required"] is False, name


def test_the_backup_sidecar_runs_only_in_its_profile() -> None:
    """Without the profile a plain `up` would start a container that cannot
    reach a repository."""
    assert _compose()["services"]["backup"]["profiles"] == ["backup"]


def test_the_sidecar_sees_the_same_data_and_socket_as_postgres() -> None:
    """pgBackRest reads the data directory and talks to the server over the
    unix socket, so both volumes are shared."""
    services = _compose()["services"]
    shared = {
        "postgres-data:/var/lib/postgresql",
        "postgres-socket:/var/run/postgresql",
    }

    assert shared <= set(services["postgres"]["volumes"])
    assert set(services["backup"]["volumes"]) == shared
    assert services["backup"]["user"] == "postgres"
    assert (
        services["backup"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    )


def test_the_sidecar_runs_the_postgres_image() -> None:
    """The backup tool must match the server's version and extensions, so the
    sidecar is the same image and never builds its own."""
    services = _compose()["services"]

    assert services["backup"]["image"] == services["postgres"]["image"]
    assert "build" not in services["backup"]


def test_pgbackrest_logs_in_as_the_database_owner() -> None:
    """With POSTGRES_USER set the image creates no `postgres` role, so
    pgBackRest's default login would be refused."""
    for name in ("postgres", "backup"):
        environment = _compose()["services"][name]["environment"]

        assert environment["PGBACKREST_PG1_USER"] == "${POSTGRES_USER}", name


def test_the_sidecar_has_no_healthcheck() -> None:
    """supercronic exposes none; the heartbeats are the health signal."""
    assert "healthcheck" not in _compose()["services"]["backup"]
