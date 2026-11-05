from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import NamedTuple

import pytest
import yaml

from scripts.ops.backup_info import (
    fork_problem,
    history_times,
    parse_target,
    restore_problem,
)
from scripts.ops.check_env import TRUTHY_VALUES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BIN = PROJECT_ROOT / "infra/backup/bin"
CRONTAB = PROJECT_ROOT / "infra/backup/crontab"
SEGMENT = "pg_wal/000000010000000000000001"
BACKUP_URL = "https://ping.example/backup"
ARCHIVE_URL = "https://ping.example/archive"
EXPIRE_URL = "https://ping.example/expire"

STUB = """#!/bin/sh
echo "$@" >> "$STUB_LOG"
[ -z "$STUB_SIDE_EFFECT" ] || eval "$STUB_SIDE_EFFECT"
case " $* " in *" $STUB_FAIL_ON "*) exit "${STUB_FAIL_CODE:-1}" ;; esac
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
            "STUB_SIDE_EFFECT": "",
            "BACKUP_HEARTBEAT_URL": BACKUP_URL,
            "BACKUP_ARCHIVE_HEARTBEAT_URL": ARCHIVE_URL,
            "BACKUP_EXPIRE_HEARTBEAT_URL": EXPIRE_URL,
            "EXPIRE_PAUSE_SECONDS": "0",
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


@pytest.mark.parametrize(
    "value",
    sorted(TRUTHY_VALUES) + sorted(v.upper() for v in TRUTHY_VALUES if v.isalpha()),
)
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


def test_run_backup_leaves_a_lock_collision_unreported(sandbox: Sandbox) -> None:
    """pgBackRest exits 50 when another backup holds the lock; that backup reports
    its own result, so a /fail here would page for a backup that is running fine."""
    result = sandbox.run(
        "run-backup", "diff", STUB_FAIL_ON="backup", STUB_FAIL_CODE="50"
    )
    assert result.returncode == 50
    assert f"{BACKUP_URL}/fail" not in sandbox.pings()


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


def test_an_open_gap_retries_the_differential_every_hour(sandbox: Sandbox) -> None:
    """The drop count does not change after a failed gap-closing backup, so the gap
    marker alone has to bring the retry back."""
    sandbox.write_drops(1)
    assert sandbox.run("check-archive", STUB_FAIL_ON="backup").returncode == 1
    sandbox.pgbackrest_log.unlink()

    result = sandbox.run("check-archive")
    assert result.returncode == 0
    assert sandbox.pgbackrest_calls() == [
        "--stanza=main check",
        "--stanza=main --type=diff backup",
    ]
    assert not sandbox.gap_open()
    assert sandbox.pings()[-1] == ARCHIVE_URL


def test_a_gap_backup_colliding_with_a_running_backup_spares_its_heartbeat(
    sandbox: Sandbox,
) -> None:
    """The backup already running closes the gap when it succeeds; until then only
    the archive heartbeat is red."""
    sandbox.write_drops(1)
    result = sandbox.run("check-archive", STUB_FAIL_ON="backup", STUB_FAIL_CODE="50")
    assert result.returncode == 1
    assert sandbox.gap_open()
    assert f"{BACKUP_URL}/fail" not in sandbox.pings()
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


EXPIRE_DATE_STUB = """#!/bin/sh
[ "$*" = "-u +%H" ] && cat "$EXPIRE_HOUR_FILE"
"""
EXPIRE_SLEEP_STUB = """#!/bin/sh
echo "$1" >> "$SLEEP_CALLS"
"""


class ExpireSandbox(Sandbox):
    """run-expire with the clock and its pause stubbed: it reads the hour on every
    attempt, so the real one would fail these tests from 20:00 UTC on."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.hour_file = root / "hour"
        self.sleep_log = root / "sleep.calls"
        self.set_hour("12")
        for name, body in (("date", EXPIRE_DATE_STUB), ("sleep", EXPIRE_SLEEP_STUB)):
            stub = self.stubs / name
            stub.write_text(body)
            stub.chmod(0o755)
        self.env["EXPIRE_HOUR_FILE"] = str(self.hour_file)
        self.env["SLEEP_CALLS"] = str(self.sleep_log)

    def set_hour(self, hour: str) -> None:
        self.hour_file.write_text(f"{hour}\n")

    def sleeps(self) -> list[str]:
        return _lines(self.sleep_log)

    def fail_attempts(self, count: int) -> str:
        """A side effect that fails the first `count` attempts of the stubbed pgbackrest."""
        failures = self.root / "failures"
        failures.write_text("x" * count)
        return (
            f'left="$(cat {failures})"; '
            f'if [ -n "$left" ]; then printf %s "${{left#x}}" > {failures}; exit 104; fi'
        )


@pytest.fixture
def expire(tmp_path: Path) -> ExpireSandbox:
    return ExpireSandbox(tmp_path)


def test_expire_that_succeeds_pings_its_own_heartbeat_only(
    expire: ExpireSandbox,
) -> None:
    result = expire.run("run-expire")
    assert result.returncode == 0
    assert expire.pgbackrest_calls() == ["--stanza=main expire"]
    assert expire.pings() == [EXPIRE_URL]


def test_expire_retries_until_an_attempt_succeeds(expire: ExpireSandbox) -> None:
    """The bucket cuts a delete batch at 60 s and keeps deleting: the next attempt goes on."""
    result = expire.run("run-expire", STUB_SIDE_EFFECT=expire.fail_attempts(2))
    assert result.returncode == 0
    assert len(expire.pgbackrest_calls()) == 3
    assert expire.pings() == [EXPIRE_URL]


def test_expire_pauses_a_minute_between_attempts(expire: ExpireSandbox) -> None:
    """Without the pause the loop hits the bucket and takes the lock back at once."""
    result = expire.run(
        "run-expire", STUB_SIDE_EFFECT=expire.fail_attempts(2), EXPIRE_PAUSE_SECONDS=""
    )
    assert result.returncode == 0
    assert expire.sleeps() == ["60", "60"]


def test_the_expire_budget_ends_well_before_the_backup() -> None:
    """The scheduled run starts at 00:00, the backup at 22:00. An hour to spare: the
    attempt under way when the budget ends still has to finish."""
    script = (BIN / "run-expire").read_text()
    match = re.search(r"EXPIRE_BUDGET_SECONDS:-(\d+)", script)
    assert match is not None
    assert int(match.group(1)) + 3600 < 22 * 3600


@pytest.mark.parametrize("code", ["104", "50"])
def test_expire_out_of_budget_pings_nothing(expire: ExpireSandbox, code: str) -> None:
    """Silence is the alert: one bad night is no incident, several turn the heartbeat
    late. Never /fail, and never the heartbeat of the backup."""
    result = expire.run(
        "run-expire",
        STUB_FAIL_ON="expire",
        STUB_FAIL_CODE=code,
        EXPIRE_BUDGET_SECONDS="0",
    )
    assert result.returncode == 1
    assert len(expire.pgbackrest_calls()) == 1
    assert expire.pings() == []
    assert "stopped trying" in result.stderr


def test_expire_stops_on_the_budget_after_several_attempts(
    expire: ExpireSandbox,
) -> None:
    """The first attempt is quick and the second outlasts the budget: only then it
    stops. The budget leaves room for a first attempt on a loaded machine."""
    second_is_slow = (
        f'[ "$(wc -l < {expire.pgbackrest_log})" -lt 2 ] || /bin/sleep 11; exit 104'
    )
    result = expire.run(
        "run-expire", STUB_SIDE_EFFECT=second_is_slow, EXPIRE_BUDGET_SECONDS="10"
    )
    assert result.returncode == 1
    assert len(expire.pgbackrest_calls()) == 2
    assert expire.pings() == []
    assert "stopped trying" in result.stderr


@pytest.mark.parametrize("hour", ["20", "21", "23"])
def test_expire_starts_nothing_from_twenty_to_midnight(
    expire: ExpireSandbox, hour: str
) -> None:
    """A run by hand late in the day must leave the lock free for the backup at 22:00."""
    expire.set_hour(hour)
    result = expire.run("run-expire")
    assert result.returncode == 1
    assert expire.pgbackrest_calls() == []
    assert expire.pings() == []
    assert "no new attempt from 20:00 UTC" in result.stderr
    assert "stopped trying" not in result.stderr


@pytest.mark.parametrize("hour", ["00", "08", "09", "19"])
def test_expire_runs_from_midnight_to_twenty(expire: ExpireSandbox, hour: str) -> None:
    """08 and 09 are not octal to the check."""
    expire.set_hour(hour)
    result = expire.run("run-expire")
    assert result.returncode == 0
    assert expire.pgbackrest_calls() == ["--stanza=main expire"]
    assert expire.pings() == [EXPIRE_URL]


def test_expire_stops_retrying_when_the_clock_reaches_the_deadline(
    expire: ExpireSandbox,
) -> None:
    expire.set_hour("19")
    # The budget ends the loop if the clock ever stops being read, and is long enough
    # for one attempt on a loaded machine.
    result = expire.run(
        "run-expire",
        STUB_SIDE_EFFECT=f"echo 20 > {expire.hour_file}; exit 104",
        EXPIRE_BUDGET_SECONDS="30",
    )
    assert result.returncode == 1
    assert len(expire.pgbackrest_calls()) == 1
    assert expire.pings() == []
    assert "no new attempt from 20:00 UTC" in result.stderr


def test_expire_stop_hour_of_24_turns_the_clock_off(expire: ExpireSandbox) -> None:
    expire.set_hour("23")
    result = expire.run("run-expire", EXPIRE_STOP_HOUR="24")
    assert result.returncode == 0
    assert expire.pgbackrest_calls() == ["--stanza=main expire"]


def test_expire_without_a_heartbeat_url_still_runs(expire: ExpireSandbox) -> None:
    result = expire.run("run-expire", BACKUP_EXPIRE_HEARTBEAT_URL="")
    assert result.returncode == 0
    assert expire.pgbackrest_calls() == ["--stanza=main expire"]
    assert expire.pings() == []


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
        "0 0 * * *": "run-expire",
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


RESTORE_COMPOSE_STUB = """#!/bin/sh
echo "$@" >> "$COMPOSE_CALLS"
for word in $STUB_FAIL_ON; do
  case " $* " in *" $word "*) exit 1 ;; esac
done
case " $* " in
  *" ps -q "*) echo container-id ;;
  *" --output=json info "*) echo "$STUB_INFO" ;;
  *" repo-ls "*) echo '{}' ;;
  *pg_is_in_recovery*) echo f ;;
  *pg_last_wal_replay_lsn*) echo "$STUB_REPLAY" ;;
esac
exit 0
"""
HEALTHY_INFO = json.dumps(
    [
        {
            "status": {"code": 0, "message": "ok"},
            "backup": [{"timestamp": {"stop": 1700000000}, "info": {"size": 1024}}],
        }
    ]
)
# Timeline 1 ends at segment 9.
HOLE_INFO = json.dumps(
    [
        {
            **json.loads(HEALTHY_INFO)[0],
            "archive": [{"id": "18-1", "max": "000000010000000000000009"}],
        }
    ]
)
HOLE_REPLAY = "0000000000000005 00000002"


class Restore(NamedTuple):
    returncode: int
    calls: list[str]
    stderr: str
    stdout: str

    def index(self, fragment: str) -> int:
        return next(i for i, call in enumerate(self.calls) if fragment in call)

    def has(self, fragment: str) -> bool:
        return any(fragment in call for call in self.calls)


def _restore(
    tmp_path: Path,
    fail_on: str = "test",
    info: str = HEALTHY_INFO,
    compose_stub: str = RESTORE_COMPOSE_STUB,
    **env: str,
) -> Restore:
    """The real script with compose and docker replaced by recorders. A stub answers 0
    by default, so `test` fails unless a case wants the gap marker to exist."""
    compose = tmp_path / "compose"
    compose.write_text(compose_stub)
    compose.chmod(0o755)
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho 0\n")
    docker.chmod(0o755)
    calls = tmp_path / "compose.calls"
    result = subprocess.run(
        [
            "/bin/bash",
            str(PROJECT_ROOT / "scripts/ops/backup_restore.sh"),
            str(compose),
        ],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMPOSE_CALLS": str(calls),
            "STUB_FAIL_ON": fail_on,
            "STUB_INFO": info,
            "CONFIRM": "1",
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return Restore(result.returncode, _lines(calls), result.stderr, result.stdout)


def test_make_backup_restore_hands_target_and_timeline_to_the_script(
    tmp_path: Path,
) -> None:
    """The real recipe and the real script, with docker replaced by a recorder: `make -n`
    would not show that the confirmation and the target reach the script."""
    shutil.copy(PROJECT_ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / ".env").write_text("")
    shutil.copytree(PROJECT_ROOT / "scripts/ops", tmp_path / "scripts/ops")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(RESTORE_COMPOSE_STUB)
    (bin_dir / "docker").chmod(0o755)
    calls = tmp_path / "compose.calls"
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "backup-restore",
            "CONFIRM=1",
            "target=2026-10-30 14:03:00+00",
            "timeline=2",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "COMPOSE_CALLS": str(calls),
            "STUB_FAIL_ON": "test",
            "STUB_INFO": HEALTHY_INFO,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    restore = next(call for call in _lines(calls) if call.endswith(" restore"))
    assert "--target=2026-10-30 14:03:00+00" in restore
    assert "--target-timeline=2" in restore


def test_a_direct_restore_needs_confirmation_too(tmp_path: Path) -> None:
    """The Makefile's guard does not cover the script called by hand or from history."""
    restore = _restore(tmp_path, CONFIRM="")
    assert restore.returncode == 2
    assert "CONFIRM=1" in restore.stderr
    assert restore.calls == []


def test_a_confirmed_restore_stops_the_writers_before_restoring(tmp_path: Path) -> None:
    restore = _restore(tmp_path, RESTORE_TARGET="2026-10-30 14:03:00+00")
    assert restore.returncode == 0, restore.stderr
    stop = restore.calls[restore.index(" stop ")]
    for service in ("app", "worker", "scheduler", "backup", "postgres"):
        assert service in stop.split()
    assert restore.index(" stop ") < restore.index("--delta")
    call = restore.calls[restore.index("--delta")]
    assert "-u postgres" in call
    assert "--target=2026-10-30 14:03:00+00" in call
    assert "--target-action=promote" in call


def test_a_restore_reports_success_only_after_recovery_ends(tmp_path: Path) -> None:
    """`up --wait` returns at the consistency point, while WAL is still replaying,
    and a target past the archived WAL leaves a server restarting in a loop."""
    restore = _restore(tmp_path)
    assert restore.returncode == 0, restore.stderr
    assert "--wait" not in restore.calls[restore.index("up -d")]
    assert restore.index("up -d") < restore.index("pg_is_in_recovery")
    assert "Next:" in restore.stdout


def test_a_failed_recovery_fails_the_restore(tmp_path: Path) -> None:
    restore = _restore(tmp_path, fail_on="test ps")
    assert restore.returncode == 1
    assert "Next:" not in restore.stdout


def test_a_restore_marks_the_stanza_before_the_server_starts(tmp_path: Path) -> None:
    """On a replacement box the volume has no stanza-ready marker, and without it the
    promoted server's new .history file is reported archived but never shipped."""
    restore = _restore(tmp_path)
    directories = restore.index("mkdir -p")
    run = restore.index("--delta")
    marker = restore.index("state/stanza-ready")
    up = restore.index("up -d")
    assert directories < run < marker < up
    for directory in ("log", "spool", "state"):
        assert (
            f"/var/lib/postgresql/pgbackrest/{directory}" in restore.calls[directories]
        )
    for line in (restore.calls[directories], restore.calls[marker]):
        assert "-u postgres" in line


def test_a_failed_preflight_stops_nothing(tmp_path: Path) -> None:
    restore = _restore(tmp_path, fail_on="test --output=json")
    assert restore.returncode != 0
    assert "cannot read the repository" in restore.stderr
    assert "nothing was stopped" in restore.stderr
    assert not restore.has(" stop ")
    assert not restore.has("--delta")


@pytest.mark.parametrize(
    "info,target,reason",
    [
        (
            json.dumps([{"status": {"code": 99, "message": "other"}, "backup": []}]),
            "",
            "not healthy",
        ),
        (
            json.dumps([{"status": {"code": 0, "message": "ok"}, "backup": []}]),
            "",
            "no backup",
        ),
        (HEALTHY_INFO, "2999-01-01 00:00:00+00", "in the future"),
        (HEALTHY_INFO, "2001-01-01 00:00:00+00", "older than the oldest"),
        (HEALTHY_INFO, "yesterday", "cannot read"),
    ],
)
def test_a_restore_that_cannot_work_stops_nothing(
    tmp_path: Path, info: str, target: str, reason: str
) -> None:
    restore = _restore(tmp_path, info=info, RESTORE_TARGET=target)
    assert restore.returncode == 1
    assert reason in restore.stderr
    assert not restore.has(" stop ")


def test_a_restore_refuses_an_open_gap(tmp_path: Path) -> None:
    """Latest would stop before the hole and older data would replace newer."""
    restore = _restore(tmp_path, fail_on="never-matches")
    assert restore.returncode == 1
    assert "RESTORE_ACCEPT_GAP=1" in restore.stderr
    assert not restore.has(" stop ")


def test_an_accepted_gap_restores(tmp_path: Path) -> None:
    restore = _restore(tmp_path, fail_on="never-matches", RESTORE_ACCEPT_GAP="1")
    assert restore.returncode == 0, restore.stderr
    assert not restore.has("state/gap-open")


def test_a_target_is_not_refused_for_an_open_gap(tmp_path: Path) -> None:
    """A target before the hole is fine, and one past it fails in recovery."""
    restore = _restore(
        tmp_path, fail_on="never-matches", RESTORE_TARGET="2026-10-30 14:03:00+00"
    )
    assert restore.returncode == 0, restore.stderr
    assert not restore.has("state/gap-open")
    assert restore.has("--delta")


def test_a_restore_to_latest_over_a_hole_fails_and_leaves_the_server(
    tmp_path: Path,
) -> None:
    """A new box has no gap-open marker; recovery ends at the hole with no error."""
    restore = _restore(tmp_path, info=HOLE_INFO, STUB_REPLAY=HOLE_REPLAY)
    assert restore.returncode == 1
    assert "FAIL: a hole in the WAL" in restore.stderr
    assert "0000000000000005" in restore.stderr
    assert "000000010000000000000009" in restore.stderr
    assert "timeline=1," in restore.stderr
    assert "Next:" not in restore.stdout
    last_up = max(i for i, call in enumerate(restore.calls) if "up -d" in call)
    stops = [i for i, call in enumerate(restore.calls) if " stop " in call]
    assert all(stop < last_up for stop in stops)


def test_an_accepted_hole_finishes_the_restore(tmp_path: Path) -> None:
    restore = _restore(
        tmp_path,
        info=HOLE_INFO,
        STUB_REPLAY=HOLE_REPLAY,
        RESTORE_ACCEPT_GAP="1",
    )
    assert restore.returncode == 0, restore.stderr
    assert "as RESTORE_ACCEPT_GAP=1 accepts" in restore.stdout
    assert "Next:" in restore.stdout


@pytest.mark.parametrize(
    "replay",
    [
        pytest.param("0000000000000009 00000002", id="replay reached the end"),
        pytest.param("000000000000000A 00000002", id="info lagged behind the archive"),
        pytest.param("0000000000000005 00000003", id="newest branch holds no segment"),
    ],
)
def test_a_restore_to_latest_without_a_hole_finishes(
    tmp_path: Path, replay: str
) -> None:
    restore = _restore(tmp_path, info=HOLE_INFO, STUB_REPLAY=replay)
    assert restore.returncode == 0, restore.stderr


@pytest.mark.parametrize(
    "env",
    [{"RESTORE_TARGET": "2026-10-30 14:03:00+00"}, {"RESTORE_TIMELINE": "1"}],
    ids=["target", "timeline"],
)
def test_only_a_restore_to_the_newest_end_compares_positions(
    tmp_path: Path, env: dict[str, str]
) -> None:
    restore = _restore(tmp_path, info=HOLE_INFO, **env)
    assert restore.returncode == 0, restore.stderr
    assert not restore.has("pg_last_wal_replay_lsn")


FORK_HISTORY = (
    "1\t0/60000A8\tno recovery target specified\n\n"
    "2\t0/70000A8\tbefore 2026-11-05 10:53:28.44235+00\n"
)
# Timeline 3 was created at 11:00:00, well after the stop it was restored to.
FORK_CREATED = {2: 1, 3: int(parse_target("2026-11-05 11:00:00+00").timestamp())}


@pytest.mark.parametrize(
    "target,refused",
    [
        ("2026-11-05 10:53:28+00", False),
        ("2026-11-05 10:53:28.44235+00", True),
        ("2026-11-05 10:54:00+00", True),
        ("2026-11-05 11:00:00+00", False),
        ("2026-11-05 12:00:00+00", False),
    ],
)
def test_a_target_only_the_parent_holds_is_refused(target: str, refused: bool) -> None:
    assert (fork_problem(FORK_HISTORY, target, FORK_CREATED) is not None) is refused


def test_a_branch_of_unknown_age_is_refused() -> None:
    assert fork_problem(FORK_HISTORY, "2026-11-05 12:00:00+00", {}) is not None


def test_a_fork_after_an_inclusive_stop_allows_the_stop_itself() -> None:
    history = "1\t0/60000A8\tafter 2026-11-05 10:53:28+00\n"
    created = {2: int(parse_target("2026-11-05 11:00:00+00").timestamp())}
    assert fork_problem(history, "2026-11-05 10:53:28+00", created) is None
    assert fork_problem(history, "2026-11-05 10:53:29+00", created) is not None


def test_forks_without_a_target_hide_nothing() -> None:
    history = "1\t0/60000A8\tno recovery target specified\n"
    assert fork_problem(history, "2026-11-05 10:54:00+00", {2: 2**40}) is None


def test_a_target_after_an_expired_history_is_on_the_branch() -> None:
    """Expire removed timeline 2's .history; timeline 3 still dates the branch."""
    history = (
        "1\t0/60000A8\tbefore 2026-11-05 10:53:28+00\n"
        "2\t0/70000A8\tno recovery target specified\n"
    )
    created = {3: int(parse_target("2026-11-05 10:54:00+00").timestamp())}
    assert fork_problem(history, "2026-11-05 10:55:00+00", created) is None
    assert fork_problem(history, "2026-11-05 10:53:50+00", created) is not None
    assert fork_problem(history, "2026-11-05 10:55:00+00", {}) is not None


def test_an_unreadable_history_fails_closed() -> None:
    problem = fork_problem("1\t0/60000A8\tat restore point x\n", "2026-11-05", {})
    assert problem is not None
    assert "cannot read" in problem


def test_the_listing_gives_timelines_by_their_hexadecimal_names() -> None:
    listing = json.dumps(
        {
            ".": {"type": "path"},
            "00000002.history": {"type": "file", "size": 1, "time": 5},
            "0000000A.history": {"type": "file", "size": 1, "time": 7},
        }
    )
    assert history_times(listing) == {2: 5, 10: 7}


def test_a_restore_with_a_target_refuses_past_a_fork_before_stopping(
    tmp_path: Path,
) -> None:
    compose = RESTORE_COMPOSE_STUB.replace(
        'case " $* " in\n  *" ps -q "*)',
        'case " $* " in\n'
        """  *" repo-ls "*) echo '{"00000002.history":{"time":4102444800}}' ;;\n"""
        "  *\" repo-get \"*) printf '1\\t0/1\\tbefore 2026-10-30 12:00:00+00\\n' ;;\n"
        '  *" ps -q "*)',
    )
    assert compose != RESTORE_COMPOSE_STUB
    info = json.dumps([{**json.loads(HEALTHY_INFO)[0], "archive": [{"id": "18-1"}]}])
    restore = _restore(
        tmp_path,
        compose_stub=compose,
        info=info,
        RESTORE_TARGET="2026-10-30 14:03:00+00",
    )
    assert restore.returncode == 1
    assert "a restore left timeline 1" in restore.stderr
    assert "timeline=1" in restore.stderr
    assert "nothing was stopped" in restore.stderr
    assert not restore.has(" stop ")


def test_a_named_timeline_skips_the_fork_check(tmp_path: Path) -> None:
    restore = _restore(
        tmp_path, RESTORE_TARGET="2026-10-30 14:03:00+00", RESTORE_TIMELINE="2"
    )
    assert restore.returncode == 0, restore.stderr
    assert not restore.has("repo-ls")
    assert "--target-timeline=2" in restore.calls[restore.index("--delta")]


def test_info_reads_the_short_offset_pgbackrest_takes() -> None:
    utc = datetime(2026, 10, 30, 14, 3, tzinfo=UTC)
    assert parse_target("2026-10-30 14:03:00+00") == utc
    assert parse_target("2026-10-30 19:03:00+05") == utc
    assert parse_target("2026-10-30 19:03:00+05:00") == utc
    assert parse_target("2026-10-30 14:03:00") == utc


def test_a_target_after_the_oldest_backup_is_accepted() -> None:
    stanza = json.loads(HEALTHY_INFO)[0]
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert restore_problem(stanza, None, now) is None
    assert restore_problem(stanza, "2026-10-30 14:03:00+00", now) is None


@pytest.mark.parametrize("command", ["check-restore", "archive-id", "newest-wal"])
def test_info_that_is_not_json_names_the_passphrase(command: str) -> None:
    """A wrong passphrase leaves pgBackRest's error text where the JSON should be."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "scripts/ops/backup_info.py"), command],
        input="ERROR: [029]: unable to decrypt\nsecond line\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == (
        "cannot parse pgbackrest info: not JSON, a wrong passphrase is the usual "
        "cause: ERROR: [029]: unable to decrypt"
    )


def test_empty_info_still_reads_as_not_json() -> None:
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "scripts/ops/backup_info.py"), "check-restore"],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "cannot parse pgbackrest info: not JSON" in result.stderr


DRILL_DOCKER_STUB = """#!/bin/sh
case "$*" in
  "exec -u postgres live test -e"*) exit "$GAP_OPEN" ;;
  "exec -u postgres live psql"*) echo 000000010000000000000007; exit 0 ;;
  "inspect -f"*) echo true; exit 0 ;;
esac
case "$1 $2" in
  "exec sidecar")
    echo "$*" >> "$PGREP_CALLS"
    if [ -e "$DRILL_DONE" ]; then exit "$EXPIRE_AT_END"; fi
    exit "$EXPIRE_AT_START"
    ;;
  "wait "*)
    touch "$DRILL_DONE"
    echo "$DRILL_STATUS"
    ;;
  "run "*)
    echo "$*" >> "$DRILL_RUNS"
    case "$*" in
      *repo-ls*) echo '{"00000002.history":{"time":4102444800}}' ;;
      *repo-get*) printf '1\\t0/1\\tbefore 2026-10-30 12:00:00+00\\n' ;;
      *--output=json*) echo '[{"archive": [{"id": "18-1"}]}]' ;;
    esac
    ;;
esac
exit 0
"""
EXPIRE_WARNING = "WARN: an expire is running in the sidecar"
EXPIRE_IN_FAILURE = "an expire ran during this drill"


def _drill(
    tmp_path: Path,
    *args: str,
    expire_at_start: int = 1,
    expire_at_end: int = 1,
    drill_status: int = 0,
    live: str = "",
    gap_open: int = 1,
) -> subprocess.CompletedProcess:
    """The real outer script, with docker answering for the sidecar and the drill."""
    stub = tmp_path / "docker"
    stub.write_text(DRILL_DOCKER_STUB)
    stub.chmod(0o755)
    env_file = tmp_path / "backup.env"
    env_file.touch()
    return subprocess.run(
        ["/bin/bash", str(VERIFY_SCRIPTS[0]), *args],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TMPDIR": str(tmp_path),
            "BACKUP_ENV_FILE": str(env_file),
            "POSTGRES_IMAGE": "image",
            "POSTGRES_USER": "user",
            "POSTGRES_DB": "db",
            "VERIFY_LIVE_CONTAINER": live,
            "VERIFY_SIDECAR_CONTAINER": "sidecar",
            "PGREP_CALLS": str(tmp_path / "pgrep.calls"),
            "DRILL_RUNS": str(tmp_path / "drill.runs"),
            "DRILL_DONE": str(tmp_path / "drill.done"),
            "EXPIRE_AT_START": str(expire_at_start),
            "EXPIRE_AT_END": str(expire_at_end),
            "DRILL_STATUS": str(drill_status),
            "GAP_OPEN": str(gap_open),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "expire_at_start, expire_at_end",
    [(0, 1), (1, 0), (0, 0)],
    ids=["seen-at-start", "seen-at-end", "seen-at-both"],
)
def test_a_failed_drill_says_an_expire_was_running(
    tmp_path: Path, expire_at_start: int, expire_at_end: int
) -> None:
    """An expire reads as damage: the operator must not take that for bad backups."""
    result = _drill(
        tmp_path,
        expire_at_start=expire_at_start,
        expire_at_end=expire_at_end,
        drill_status=1,
    )
    assert result.returncode == 1
    assert EXPIRE_IN_FAILURE in result.stderr
    assert "06:00 and 20:00 UTC" in result.stderr


def test_a_drill_warns_when_an_expire_runs_at_its_start(tmp_path: Path) -> None:
    result = _drill(tmp_path, expire_at_start=0, expire_at_end=0)
    assert result.returncode == 0
    assert EXPIRE_WARNING in result.stdout
    assert EXPIRE_IN_FAILURE not in result.stderr


# 1: pgrep found nothing. 125: docker, no such container or a stopped one. 127: an image
# without pgrep. None of them is a reason to fail, or to blame an expire.
@pytest.mark.parametrize("sidecar_status", [1, 125, 127])
def test_a_drill_without_an_expire_says_nothing_of_one(
    tmp_path: Path, sidecar_status: int
) -> None:
    result = _drill(
        tmp_path, expire_at_start=sidecar_status, expire_at_end=sidecar_status
    )
    assert result.returncode == 0
    assert EXPIRE_WARNING not in result.stdout


@pytest.mark.parametrize("sidecar_status", [1, 125])
def test_a_failed_drill_without_an_expire_blames_none(
    tmp_path: Path, sidecar_status: int
) -> None:
    result = _drill(
        tmp_path,
        expire_at_start=sidecar_status,
        expire_at_end=sidecar_status,
        drill_status=1,
    )
    assert result.returncode == 1
    assert EXPIRE_IN_FAILURE not in result.stderr


def test_the_drill_asks_the_sidecar_with_a_pattern_that_misses_itself(
    tmp_path: Path,
) -> None:
    _drill(tmp_path)
    calls = _lines(tmp_path / "pgrep.calls")
    assert calls
    assert set(calls) == {"exec sidecar pgrep -f [r]un-expire"}


def test_the_image_ships_the_pgrep_the_drill_asks_with() -> None:
    """The base image has none, and its absence reads as no expire running."""
    dockerfile = (PROJECT_ROOT / "infra/postgres/Dockerfile").read_text()
    assert re.search(r"apt-get install .*\bprocps\b", dockerfile)


def test_the_drill_fails_on_a_gap_open_on_the_live_server(tmp_path: Path) -> None:
    result = _drill(tmp_path, live="live", gap_open=0)
    assert result.returncode == 1
    assert "a gap is open" in result.stderr
    assert _lines(tmp_path / "drill.runs") == []


def test_a_drill_to_a_target_is_not_failed_by_an_open_gap(tmp_path: Path) -> None:
    result = _drill(
        tmp_path,
        "--target",
        "2026-10-30 14:03:00+00",
        "--timeline",
        "1",
        live="live",
        gap_open=0,
    )
    assert result.returncode == 0, result.stderr


def test_the_drill_hands_what_the_live_server_archived_to_the_container(
    tmp_path: Path,
) -> None:
    result = _drill(tmp_path, live="live")
    assert result.returncode == 0, result.stderr
    drill = next(call for call in _lines(tmp_path / "drill.runs") if "--name" in call)
    assert "-e LIVE_ARCHIVED_WAL=000000010000000000000007" in drill


def test_the_drill_without_a_live_server_says_it_compares_nothing(
    tmp_path: Path,
) -> None:
    result = _drill(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "no live server answers" in result.stdout
    drill = next(call for call in _lines(tmp_path / "drill.runs") if "--name" in call)
    assert "-e LIVE_ARCHIVED_WAL= " in drill


def test_the_drill_refuses_a_target_past_a_fork(tmp_path: Path) -> None:
    result = _drill(tmp_path, "--target", "2026-10-30 14:03:00+00")
    assert result.returncode == 1
    assert "a restore left timeline 1" in result.stderr
    assert not any("--name" in call for call in _lines(tmp_path / "drill.runs"))


def test_a_drill_on_a_named_timeline_skips_the_fork_check(tmp_path: Path) -> None:
    result = _drill(tmp_path, "--target", "2026-10-30 14:03:00+00", "--timeline", "1")
    assert result.returncode == 0, result.stderr
    runs = _lines(tmp_path / "drill.runs")
    assert not any("repo-ls" in call for call in runs)
    drill = next(call for call in runs if "--name" in call)
    assert "-e RESTORE_TIMELINE=1" in drill


def test_the_drill_checks_the_repository_by_the_text_of_verify() -> None:
    """verify exits 0 over a damaged object and a missing manifest."""
    inner = VERIFY_SCRIPTS[1].read_text()
    assert "pgbackrest --stanza=main --verbose verify 2>&1 | tee" in inner
    assert "status: ok$" in inner
    assert "a backup that cannot be restored" in inner
    assert "found in the repository but not in backup" in inner
    assert inner.index("--verbose verify") < inner.index('"$@" restore')


def test_the_drill_requires_replay_to_reach_what_the_live_server_archived() -> None:
    """Replay stops at a hole with no error, on data that can still look recent."""
    outer, inner = (path.read_text() for path in VERIFY_SCRIPTS)
    assert "last_archived_wal" in outer
    assert '[[ "$replay_segment" < "${LIVE_ARCHIVED_WAL:8}" ]]' in inner
    assert inner.index("a hole in the WAL") < inner.index('"[verify] OK"')


def test_a_restart_loop_tells_the_operator_how_to_stop_it() -> None:
    """Under `restart: always` a failed recovery keeps restarting after the script
    exits, so the message has to say how to stop it and what to try next."""
    script = (PROJECT_ROOT / "scripts/ops/backup_restore_wait.sh").read_text()
    assert r"stop it with \`${COMPOSE[*]} stop postgres\`" in script
    assert "with an earlier target" in script


def test_a_cancelled_drill_exits_with_the_signal_convention() -> None:
    """Ctrl-C reads as 130 and SIGTERM as 143 to whatever ran the drill."""
    script = (PROJECT_ROOT / "scripts/ops/backup_verify.sh").read_text()
    assert "trap 'exit 130' INT" in script
    assert "trap 'exit 143' TERM" in script


def test_postgres_runs_under_an_init_process() -> None:
    """Without it an S3 outage crash-restarts the cluster: the async archiver is an
    orphan of the postmaster, which reads its nonzero exit as a crashed child."""
    for compose in (
        "infra/docker-compose.yml",
        "infra/backup/smoke/docker-compose.yml",
    ):
        services = yaml.safe_load((PROJECT_ROOT / compose).read_text())["services"]
        assert services["postgres"]["init"] is True, compose
    assert "--init" in (PROJECT_ROOT / "scripts/ops/backup_verify.sh").read_text()


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
        ["make", "--no-print-directory", "-n", *goal.split()],
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


def _bucket(
    tmp_path: Path, command: str, lifecycle_answer: str = ""
) -> subprocess.CompletedProcess[str]:
    """The real script with docker replaced by a recorder."""
    (tmp_path / "docker").write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$DOCKER_CALLS"\n'
        'case " $* " in *" get-bucket-lifecycle-configuration "*)\n'
        f"  {lifecycle_answer} ;;\n"
        "esac\n"
    )
    (tmp_path / "docker").chmod(0o755)
    return subprocess.run(
        [
            "/bin/bash",
            str(PROJECT_ROOT / "scripts/ops/backup_bucket.sh"),
            command,
            "bucket",
            "hel1",
        ],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DOCKER_CALLS": str(tmp_path / "docker.calls"),
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_bucket_script_passes_the_checksum_settings_to_the_container(
    tmp_path: Path,
) -> None:
    """aws-cli's default checksums are not what S3-compatible storage accepts."""
    assert _bucket(tmp_path, "apply").returncode == 0
    runs = _lines(tmp_path / "docker.calls")
    assert len(runs) == 2
    for run in runs:
        assert "-e AWS_REQUEST_CHECKSUM_CALCULATION=when_required" in run
        assert "-e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required" in run
        assert "secret" not in run


def test_show_prints_no_lifecycle_where_aws_cli_crashes(tmp_path: Path) -> None:
    """What Hetzner answers for a bucket with no rules, and aws-cli's crash on it."""
    result = _bucket(
        tmp_path,
        "show",
        """echo "aws: [ERROR]: argument of type 'NoneType' is not a container" >&2; exit 255""",
    )
    assert result.returncode == 0
    assert "no lifecycle" in result.stdout
    assert "console" in result.stdout


def test_show_fails_on_any_other_lifecycle_error(tmp_path: Path) -> None:
    result = _bucket(tmp_path, "show", 'echo "AccessDenied" >&2; exit 255')
    assert result.returncode == 1
    assert "AccessDenied" in result.stderr


def test_show_prints_the_lifecycle_it_reads(tmp_path: Path) -> None:
    result = _bucket(tmp_path, "show", """echo '{"Rules": [{"ID": "noncurrent"}]}'""")
    assert result.returncode == 0
    assert '{"Rules": [{"ID": "noncurrent"}]}' in result.stdout
    assert "no lifecycle" not in result.stdout


def test_the_smoke_images_are_pinned() -> None:
    """MinIO publishes no community image any more; the smoke runs a rebuilt fork,
    so a tag alone would let whoever controls that repository swap the binary."""
    services = yaml.safe_load(SMOKE_COMPOSE.read_text())["services"]
    pulled = [
        s["image"]
        for s in services.values()
        if "build" not in s and s.get("pull_policy") != "never"
    ]
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


def test_the_smoke_backs_up_through_the_sidecar_shape() -> None:
    """Backing up from inside the server container would skip the socket volume
    the deployed sidecar reaches the server through."""
    smoke = yaml.safe_load(SMOKE_COMPOSE.read_text())["services"]
    deployed = yaml.safe_load((PROJECT_ROOT / "infra/docker-compose.yml").read_text())[
        "services"
    ]
    assert smoke["backup"]["volumes"] == deployed["backup"]["volumes"]
    assert smoke["backup"]["user"] == deployed["backup"]["user"]
    assert smoke["postgres"]["volumes"] == deployed["postgres"]["volumes"]
    script = (PROJECT_ROOT / "scripts/ops/backup_smoke.sh").read_text()
    assert "run --rm --no-deps -T backup run-backup full" in script
