import configparser
from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTGRESQL_CONF = PROJECT_ROOT / "infra/postgres/postgresql.conf"
POSTGRES_DOCKERFILE = PROJECT_ROOT / "infra/postgres/Dockerfile"
PGBACKREST_CONF = PROJECT_ROOT / "infra/backup/pgbackrest.conf"


def _postgres_settings() -> dict[str, str]:
    settings: dict[str, str] = {}
    for line in POSTGRESQL_CONF.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(\w+)\s*=\s*(.+?)\s*$", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def _pgbackrest() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(PGBACKREST_CONF, encoding="utf-8")
    return parser


def test_archiving_is_on_and_goes_through_the_wrapper() -> None:
    """WAL that skips the wrapper never reaches the repository, and a restore
    then stops at the last archived segment."""
    settings = _postgres_settings()
    assert settings["archive_mode"] == "on"
    assert settings["archive_command"] == "'/usr/local/bin/pgbackrest-archive-push %p'"
    assert settings["archive_timeout"] == "60"
    text = POSTGRESQL_CONF.read_text(encoding="utf-8")
    assert text.index("archive_command") < text.index("include_dir")


def test_the_archive_command_names_pgbackrest() -> None:
    """pgBackRest's check and backup refuse an archive_command without its name."""
    assert "pgbackrest" in _postgres_settings()["archive_command"]


def test_pgbackrest_config_holds_no_credentials() -> None:
    """Credentials and the bucket come from PGBACKREST_* env, not a tracked file."""
    forbidden = {"key", "key-secret", "cipher-pass", "bucket", "endpoint", "region"}
    parser = _pgbackrest()
    for section in parser.sections():
        for option in parser.options(section):
            assert option.removeprefix("repo1-s3-") not in forbidden
            assert option.removeprefix("repo1-") not in forbidden


def test_pgbackrest_keeps_its_state_on_the_data_volume() -> None:
    """postgres-data is mounted at /var/lib/postgresql; state anywhere else is
    lost on container recreate, the log (the record of a dropped segment) with it."""
    options = _pgbackrest()["global"]
    for option in ("spool-path", "log-path", "lock-path"):
        assert options[option].startswith("/var/lib/postgresql/"), option


def test_retention_covers_fourteen_days() -> None:
    options = _pgbackrest()["global"]
    assert options["repo1-retention-full-type"] == "time"
    assert options["repo1-retention-full"] == "14"


def test_the_image_installs_certificates_and_verifies_supercronic() -> None:
    """Without ca-certificates every S3 call fails on the issuer check."""
    dockerfile = POSTGRES_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        r"install -y --no-install-recommends[^\n]*ca-certificates", dockerfile
    )
    assert "pgbackrest" in dockerfile
    assert "sha1sum -c" in dockerfile
