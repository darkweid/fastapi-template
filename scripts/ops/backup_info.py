#!/usr/bin/env python3
"""Reads `pgbackrest --output=json info` from stdin, for the restore scripts.

check-restore [target]  exits 1 with the reason unless a restore can succeed: a
                        healthy stanza with a backup, and a target that is neither
                        in the future nor older than the oldest backup. pgBackRest
                        and PostgreSQL find both out only after the services stop.
archive-id              prints the id of the newest archive, for reading its .history files.
newest-wal              prints the newest segment the repository holds, if any.
newest-history <listing>  prints the newest .history in a JSON repo-ls listing; no stdin.
check-fork <target> <listing>  reads a timeline .history file from stdin instead (not
                        info) and exits 1 when the target lies only on a parent of it.

Runs on the host next to check_env.py, so it stays on the standard library.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sys
from typing import Any

SHORT_OFFSET = re.compile(r"([+-]\d{2})$")
# PostgreSQL trims trailing zeros; Python before 3.11 reads three or six digits only.
FRACTION = re.compile(r"\.(\d{1,6})(?=[+-]|$)")
# parent timeline, switch position, reason (src/backend/access/transam/xlog.c).
HISTORY_LINE = re.compile(r"^(\d+)\s+[0-9A-Fa-f]+/[0-9A-Fa-f]+\s+(.*)$")
STOP_REASON = re.compile(r"^(before|after) (.+)$")
NO_TARGET_REASON = "no recovery target specified"


def parse_target(target: str) -> datetime:
    """A target as pgBackRest takes it; without an offset it is UTC, the server's zone."""
    padded = FRACTION.sub(lambda m: "." + m.group(1).ljust(6, "0"), target.strip())
    moment = datetime.fromisoformat(SHORT_OFFSET.sub(r"\1:00", padded))
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def restore_problem(
    stanza: dict[str, Any], target: str | None, now: datetime
) -> str | None:
    status = stanza["status"]
    if status["code"] != 0:
        return f"the stanza is not healthy: {status['message']}"
    backups = stanza["backup"]
    if not backups:
        return "the repository holds no backup"
    if not target:
        return None
    try:
        moment = parse_target(target)
    except ValueError:
        return f"cannot read target '{target}'; use '2026-10-30 14:03:00+00'"
    if moment > now:
        return f"target '{target}' is in the future; omit it to restore to the latest point"
    oldest_stop = min(backup["timestamp"]["stop"] for backup in backups)
    oldest = datetime.fromtimestamp(oldest_stop, tz=timezone.utc)
    if moment < oldest:
        return f"target '{target}' is older than the oldest backup, which ended at {oldest}"
    return None


def history_times(listing: str) -> dict[int, int]:
    """Timeline number to the time its .history reached the repository, from
    `pgbackrest --output=json repo-ls`. The file's name is the number in hexadecimal."""
    return {
        int(name.split(".")[0], 16): entry["time"]
        for name, entry in json.loads(listing).items()
        if name.endswith(".history")
    }


def fork_problem(history: str, target: str, created: dict[int, int]) -> str | None:
    """A moment after a restore's stop but before its branch existed lies only on the
    parent, and a recovery following this branch never replays it: the server comes up on
    older data with exit 0. A moment after the branch was created lies on the branch and
    is fine. A fork with no target (a restore to latest) ended at the end of its
    parent and hides nothing. A branch whose .history arrived late only refuses more.
    Expire removes old .history files; timeline numbers only grow with time, so the next
    one still in the repository bounds the time of a removed one."""
    moment = parse_target(target)
    lines = [
        line.strip()
        for line in history.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    parsed = [HISTORY_LINE.match(line) for line in lines]
    parents = [int(match.group(1)) if match else None for match in parsed]
    children = [*parents[1:], max(created, default=None)]
    for line, match, child in zip(lines, parsed, children, strict=True):
        reason = match.group(2).strip() if match else ""
        if reason == NO_TARGET_REASON:
            continue
        stop = STOP_REASON.match(reason)
        if not match or not stop:
            return f"cannot read the timeline history line '{line}'"
        stopped_at = parse_target(stop.group(2))
        passed = (
            moment >= stopped_at if stop.group(1) == "before" else moment > stopped_at
        )
        born = [
            time for number, time in created.items() if child is None or number >= child
        ]
        if passed and (not born or moment.timestamp() < min(born)):
            return (
                f"a restore left timeline {match.group(1)} at {stop.group(2)}, and target "
                f"'{target}' lies between that and the restore itself: only that "
                "timeline holds it, and a restore follows the newest branch, which stops "
                f"at the fork with older data. Name the timeline with timeline={match.group(1)}"
            )
    return None


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "newest-history" and len(sys.argv) == 3:
        times = history_times(sys.argv[2])
        if times:
            print(f"{max(times):08X}.history")
        return 0
    if command == "check-fork" and len(sys.argv) == 4:
        try:
            problem = fork_problem(
                sys.stdin.read(), sys.argv[2], history_times(sys.argv[3])
            )
        except ValueError as error:
            problem = f"cannot read a time in the timeline check: {error}"
        if problem:
            print(problem, file=sys.stderr)
            return 1
        return 0
    info = sys.stdin.read()
    try:
        stanza = json.loads(info)[0]
    except ValueError:
        first_line = info.partition("\n")[0]
        print(
            "cannot parse pgbackrest info: not JSON, a wrong passphrase is the usual "
            f"cause: {first_line}",
            file=sys.stderr,
        )
        return 1
    if command == "check-restore":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        problem = restore_problem(stanza, target, datetime.now(timezone.utc))
        if problem:
            print(problem, file=sys.stderr)
            return 1
        return 0
    if command == "archive-id":
        archives = stanza.get("archive") or [{"id": ""}]
        print(archives[-1]["id"])
        return 0
    if command == "newest-wal":
        archives = stanza.get("archive") or [{}]
        print(archives[-1].get("max") or "")
        return 0
    print(
        "usage: backup_info.py check-restore [target] | archive-id | newest-wal"
        " | newest-history <listing> | check-fork <target> <listing>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
