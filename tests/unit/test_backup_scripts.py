from pathlib import Path
import re
import subprocess

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BIN = PROJECT_ROOT / "infra/backup/bin"
CRONTAB = PROJECT_ROOT / "infra/backup/crontab"
SEGMENT = "pg_wal/000000010000000000000001"
BACKUP_URL = "https://ping.example/backup"
ARCHIVE_URL = "https://ping.example/archive"

STUB = """#!/bin/sh
echo "$@" >> "$STUB_LOG"
case " $* " in *" $STUB_FAIL_ON "*) exit 1 ;; esac
exit 0
"""


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.logs = root / "log"
        self.pgbackrest_log = root / "pgbackrest.calls"
        self.curl_log = root / "curl.calls"
        self.logs.mkdir()
        stubs = root / "stubs"
        stubs.mkdir()
        for name in ("pgbackrest", "curl"):
            stub = stubs / name
            stub.write_text(STUB.replace("$STUB_LOG", f"${name.upper()}_CALLS"))
            stub.chmod(0o755)
        self.stubs = stubs
        self.env = {
            "PATH": f"{stubs}:/usr/bin:/bin",
            "STATE_DIR": str(self.state),
            "LOG_DIR": str(self.logs),
            "PGBACKREST_CALLS": str(self.pgbackrest_log),
            "CURL_CALLS": str(self.curl_log),
            "STUB_FAIL_ON": "never-matches",
            "BACKUP_HEARTBEAT_URL": BACKUP_URL,
            "BACKUP_ARCHIVE_HEARTBEAT_URL": ARCHIVE_URL,
        }

    def mark_stanza_ready(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "stanza-ready").touch()

    def set_dropped_count(self, count: int, inode: int | None = None) -> None:
        """Stores the count against the current log's inode unless one is given."""
        self.state.mkdir(parents=True, exist_ok=True)
        if inode is None:
            inode = (self.logs / "main-archive-push-async.log").stat().st_ino
        (self.state / "dropped-count").write_text(f"{inode} {count}\n")

    def write_drops(self, count: int) -> None:
        lines = [
            f"WARN: dropped WAL file '00000001000000000000000{i}' because archive queue exceeded 32MB"
            for i in range(count)
        ]
        lines.append("INFO: unrelated line")
        (self.logs / "main-archive-push-async.log").write_text("\n".join(lines) + "\n")

    def run(
        self, script: str, *args: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(BIN / script), *args],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def pgbackrest_calls(self) -> list[str]:
        return _lines(self.pgbackrest_log)

    def pings(self) -> list[str]:
        return [line.split()[-1] for line in _lines(self.curl_log)]

    def stored_count(self) -> int:
        return int((self.state / "dropped-count").read_text().split()[1])

    def gap_open(self) -> bool:
        return (self.state / "gap-open").exists()


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


@pytest.mark.parametrize(
    "env", [{}, {"BACKUP_ENABLED": "false"}, {"BACKUP_ENABLED": "0"}]
)
def test_archive_wal_ships_nothing_while_disabled(
    sandbox: Sandbox, env: dict[str, str]
) -> None:
    """A deploy without backups must keep archiving a no-op, or PostgreSQL retries forever."""
    sandbox.mark_stanza_ready()
    result = sandbox.run("pgbackrest-archive-push", SEGMENT, **env)
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == []


def test_archive_wal_ships_nothing_before_the_stanza_exists(sandbox: Sandbox) -> None:
    """The init server of a fresh volume cannot stop while archiving fails."""
    result = sandbox.run("pgbackrest-archive-push", SEGMENT, BACKUP_ENABLED="true")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == []


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1", "t", "y"])
def test_archive_wal_ships_for_every_truthy_spelling(
    sandbox: Sandbox, value: str
) -> None:
    """The set must match scripts/ops/check_env.py, or a deploy gate and the wrapper disagree."""
    sandbox.mark_stanza_ready()
    result = sandbox.run("pgbackrest-archive-push", SEGMENT, BACKUP_ENABLED=value)
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == [f"--stanza=main archive-push {SEGMENT}"]


def test_archive_wal_reports_a_failed_push(sandbox: Sandbox) -> None:
    """A non-zero exit makes PostgreSQL retry the segment instead of recycling it."""
    sandbox.mark_stanza_ready()
    result = sandbox.run(
        "pgbackrest-archive-push",
        SEGMENT,
        BACKUP_ENABLED="true",
        STUB_FAIL_ON="archive-push",
    )
    assert result.returncode == 1


def test_run_backup_pings_start_then_success(sandbox: Sandbox) -> None:
    result = sandbox.run("run-backup", "full")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == ["--stanza=main --type=full backup"]
    assert sandbox.pings() == [f"{BACKUP_URL}/start", BACKUP_URL]


def test_run_backup_pings_fail_and_exits_nonzero(sandbox: Sandbox) -> None:
    result = sandbox.run("run-backup", "diff", STUB_FAIL_ON="backup")
    assert result.returncode == 1
    assert sandbox.pings() == [f"{BACKUP_URL}/start", f"{BACKUP_URL}/fail"]


def test_run_backup_rejects_an_unknown_type(sandbox: Sandbox) -> None:
    result = sandbox.run("run-backup", "incr")
    assert result.returncode == 2
    assert sandbox.pgbackrest_calls() == []
    assert sandbox.pings() == []


def test_run_backup_success_closes_the_gap(sandbox: Sandbox) -> None:
    """A new backup is what bridges a dropped segment, so it clears the marker."""
    sandbox.state.mkdir()
    (sandbox.state / "gap-open").touch()
    assert sandbox.run("run-backup", "diff").returncode == 0
    assert not sandbox.gap_open()


def test_check_archive_pings_fail_when_check_fails(sandbox: Sandbox) -> None:
    result = sandbox.run("check-archive", STUB_FAIL_ON="check")
    assert result.returncode == 1
    assert sandbox.pings() == [f"{ARCHIVE_URL}/fail"]


def test_check_archive_with_no_log_and_no_state_is_green(sandbox: Sandbox) -> None:
    result = sandbox.run("check-archive")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == ["--stanza=main check"]
    assert sandbox.pings() == [ARCHIVE_URL]


def test_new_drops_open_a_gap_and_start_a_differential(sandbox: Sandbox) -> None:
    sandbox.write_drops(2)
    result = sandbox.run("check-archive")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == [
        "--stanza=main check",
        "--stanza=main --type=diff backup",
    ]
    assert sandbox.stored_count() == 2
    assert not sandbox.gap_open()
    assert sandbox.pings()[0] == f"{ARCHIVE_URL}/fail"


def test_old_drops_are_not_counted_twice(sandbox: Sandbox) -> None:
    sandbox.write_drops(2)
    sandbox.set_dropped_count(2)
    result = sandbox.run("check-archive")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == ["--stanza=main check"]
    assert sandbox.pings() == [ARCHIVE_URL]


def test_a_truncated_log_resets_the_count(sandbox: Sandbox) -> None:
    sandbox.write_drops(1)
    sandbox.set_dropped_count(5)
    assert sandbox.run("check-archive").returncode == 0
    assert sandbox.stored_count() == 1
    assert sandbox.pgbackrest_calls() == ["--stanza=main check"]

    sandbox.write_drops(2)
    assert sandbox.run("check-archive").returncode == 0
    assert "--stanza=main --type=diff backup" in sandbox.pgbackrest_calls()


@pytest.mark.parametrize("content", ["", "garbage", "12 x", "5"])
def test_an_unreadable_count_does_not_blind_the_check(
    sandbox: Sandbox, content: str
) -> None:
    """A state file emptied by a full disk or a crash must not hide later drops."""
    sandbox.write_drops(2)
    sandbox.state.mkdir()
    (sandbox.state / "dropped-count").write_text(content)
    assert sandbox.run("check-archive").returncode == 0
    assert "--stanza=main --type=diff backup" in sandbox.pgbackrest_calls()
    assert sandbox.stored_count() == 2


def test_a_rotated_log_counts_its_drops_as_new(sandbox: Sandbox) -> None:
    """A fresh log with fewer drops than the old count must not hide them."""
    sandbox.write_drops(1)
    sandbox.set_dropped_count(5, inode=1)
    assert sandbox.run("check-archive").returncode == 0
    assert "--stanza=main --type=diff backup" in sandbox.pgbackrest_calls()
    assert sandbox.stored_count() == 1


def test_an_open_gap_keeps_the_heartbeat_red(sandbox: Sandbox) -> None:
    sandbox.write_drops(1)
    result = sandbox.run("check-archive", STUB_FAIL_ON="backup")
    assert result.returncode == 1
    assert sandbox.gap_open()
    assert sandbox.pings()[-1] == f"{ARCHIVE_URL}/fail"


def test_an_empty_heartbeat_url_pings_nothing(sandbox: Sandbox) -> None:
    assert sandbox.run("run-backup", "full", BACKUP_HEARTBEAT_URL="").returncode == 0
    assert sandbox.run("check-archive", BACKUP_ARCHIVE_HEARTBEAT_URL="").returncode == 0
    assert sandbox.pings() == []


def test_a_failed_ping_never_changes_the_exit_status(sandbox: Sandbox) -> None:
    """A heartbeat outage must not turn a good backup into a failed job."""
    result = sandbox.run(
        "run-backup",
        "full",
        STUB_FAIL_ON="never-matches",
        PATH=f"{_failing_curl(sandbox)}:{sandbox.env['PATH']}",
    )
    assert result.returncode == 0


def _failing_curl(sandbox: Sandbox) -> Path:
    directory = sandbox.root / "failing"
    directory.mkdir()
    curl = directory / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    curl.chmod(0o755)
    return directory


def test_crontab_runs_the_spec_schedule() -> None:
    """Times are UTC: 22:00 is 03:00 in Asia/Tashkent."""
    entries = [
        line.split(None, 5)
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    schedule: dict[str, str] = {" ".join(parts[:5]): parts[5] for parts in entries}
    assert schedule == {
        "0 22 * * 0": "run-backup full",
        "0 22 * * 1-6": "run-backup diff",
        "17 * * * *": "check-archive",
    }


VERIFY_SCRIPTS = (
    PROJECT_ROOT / "scripts/ops/backup_verify.sh",
    PROJECT_ROOT / "scripts/ops/backup_verify_in_container.sh",
)
SMOKE_COMPOSE = PROJECT_ROOT / "infra/backup/smoke/docker-compose.yml"
PINNED_IMAGE = re.compile(r"^[\w./-]+:RELEASE\.[\w.-]+@sha256:[0-9a-f]{64}$")


def test_the_verification_restore_never_archives() -> None:
    """A drill server that archived would push a second timeline of WAL into the
    live stanza, beside the history it was restored from."""
    outer, inner = (path.read_text() for path in VERIFY_SCRIPTS)
    assert "-e BACKUP_ENABLED=false" in outer
    assert "--archive-mode=off" in inner


def test_the_verification_restore_never_touches_the_live_volume() -> None:
    for path in VERIFY_SCRIPTS:
        assert "postgres-data" not in path.read_text()


def test_a_live_restore_needs_confirmation() -> None:
    """`make -n` executes nothing, so the refusal has to happen while make expands
    the recipe, before any container is stopped."""
    result = subprocess.run(
        ["make", "-n", "backup-restore", "target=x"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "CONFIRM=1" in result.stderr


def test_a_confirmed_restore_stops_the_writers_before_restoring() -> None:
    result = subprocess.run(
        ["make", "-n", "backup-restore", "target=2026-10-30 14:03:00+00", "CONFIRM=1"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    stop = next(i for i, line in enumerate(lines) if " stop " in line)
    restore = next(i for i, line in enumerate(lines) if line.endswith(" restore"))
    assert stop < restore
    for service in ("app", "worker", "scheduler", "backup", "postgres"):
        assert service in lines[stop].split()
    words = lines[restore].split()
    assert words[words.index("-u") + 1] == "postgres"
    assert "--delta" in words
    assert "--target='2026-10-30 14:03:00+00'" in lines[restore]
    assert "--target-action=promote" in lines[restore]


def test_a_restore_reports_success_only_after_recovery_ends() -> None:
    """`up --wait` returns at the consistency point, while WAL is still replaying,
    and a target past the archived WAL leaves a server restarting in a loop."""
    result = subprocess.run(
        ["make", "-n", "backup-restore", "CONFIRM=1"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    up = next(i for i, line in enumerate(lines) if " up " in line)
    waits = next(i for i, line in enumerate(lines) if "backup_restore_wait.sh" in line)
    following = next(i for i, line in enumerate(lines) if "Next:" in line)
    assert "--wait" not in lines[up]
    assert up < waits < following


def test_a_refused_restore_builds_nothing() -> None:
    result = subprocess.run(
        ["make", "-n", "backup-restore"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "build" not in result.stdout


@pytest.mark.parametrize(
    "goal",
    [
        "backup-info",
        "backup-now type=full",
        "backup-verify",
        "backup-restore CONFIRM=1",
    ],
)
def test_every_backup_target_builds_the_image_first(goal: str) -> None:
    """The sidecar never pulls (`pull_policy: never`), so on a fresh box the image
    exists only once something builds it."""
    result = subprocess.run(
        ["make", "-n", *goal.split()],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines()[0].endswith(" build postgres")


def test_a_manual_backup_pings_no_heartbeat() -> None:
    """A manual run that fails must not page as if the schedule had failed."""
    result = subprocess.run(
        ["make", "-n", "backup-now", "type=diff"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "-e BACKUP_HEARTBEAT_URL= " in result.stdout


def test_the_drill_container_is_capped() -> None:
    outer = VERIFY_SCRIPTS[0].read_text()
    assert '--memory "${VERIFY_MEMORY:-1g}"' in outer
    assert '--cpus "${VERIFY_CPUS:-1}"' in outer


def test_the_smoke_images_are_pinned() -> None:
    """MinIO publishes no community image any more; the smoke runs a rebuilt fork,
    so a tag alone would let whoever controls that repository swap the binary."""
    services = yaml.safe_load(SMOKE_COMPOSE.read_text())["services"]
    pulled = [s["image"] for s in services.values() if "build" not in s]
    assert pulled
    for image in pulled:
        assert PINNED_IMAGE.match(image), image


def test_the_smoke_never_claims_a_global_name() -> None:
    """A container_name or a fixed network name would collide with a stack, or a
    second smoke, running on the same daemon."""
    loaded = yaml.safe_load(SMOKE_COMPOSE.read_text())
    for service in loaded["services"].values():
        assert "container_name" not in service
        assert "ports" not in service
    assert "networks" not in loaded
