#!/usr/bin/env bash
# The live restore (`make backup-restore`): overwrites PGDATA with a backup, replays WAL
# to RESTORE_TARGET or to the end of the archive, and starts the server on it.
#
#   CONFIRM=1 RESTORE_TARGET="2026-10-30 14:03:00+00" scripts/ops/backup_restore.sh docker compose ...
#
# Everything that can refuse runs before anything stops. It works on a fresh box. The
# pgBackRest directories come first, or a fresh volume restores without a log file. The
# stanza-ready marker is written before the server starts: a fresh volume has none, and
# without it the archive command reports the new timeline's .history file archived
# without shipping it, so a second loss would restore the old timeline and drop this
# box's writes. A restore from the stanza is what proves this cluster owns it.
#
#   RESTORE_ACCEPT_GAP=1   restore to latest although the WAL has a hole: latest stops
#                          before it and older data replaces newer
#   RESTORE_TIMELINE=N     follow timeline N instead of the newest, which skips the
#                          refusal of a target past a fork
#   RESTORE_STOP_SERVICES  what writes to the database (the smoke has none of them)
set -euo pipefail

[ "$#" -gt 0 ] || { echo "usage: $0 <compose command>" >&2; exit 2; }
if [ "${CONFIRM:-}" != 1 ]; then
  echo "[restore] this overwrites the live database; run again with CONFIRM=1" >&2
  exit 2
fi
COMPOSE=("$@")
TARGET="${RESTORE_TARGET:-}"
TIMELINE="${RESTORE_TIMELINE:-}"
read -r -a STOP_SERVICES <<< "${RESTORE_STOP_SERVICES-app worker scheduler}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
PGBACKREST_DIR=/var/lib/postgresql/pgbackrest
RUN=("${COMPOSE[@]}" run --rm --no-deps -T -u postgres postgres)

phase=preflight
report_failure() {
  status=$?
  [ "$status" -ne 0 ] || return 0
  case "$phase" in
    preflight)
      echo "[restore] refused: nothing was stopped or changed" >&2
      ;;
    restoring)
      echo "[restore] FAIL: the services are stopped and PGDATA may be half written: fix the cause and run the restore again" >&2
      ;;
  esac
}
trap report_failure EXIT

echo "[restore] checking the repository before stopping anything"
"${RUN[@]}" mkdir -p "$PGBACKREST_DIR/log" "$PGBACKREST_DIR/spool" "$PGBACKREST_DIR/state"
# pgBackRest's errors arrive on stdout.
if ! info="$("${RUN[@]}" pgbackrest --stanza=main --output=json info)"; then
  echo "[restore] cannot read the repository: $info" >&2
  exit 1
fi
python3 "$SCRIPTS/backup_info.py" check-restore ${TARGET:+"$TARGET"} <<< "$info"
if [ -n "$TARGET" ] && [ -z "$TIMELINE" ]; then
  "$SCRIPTS/backup_check_fork.sh" "$TARGET" "$info" "${RUN[@]}" pgbackrest
fi
# A target with a commit after it ahead of the hole is not affected; any later one stops
# the server in recovery.
if [ -z "$TARGET" ] && [ "${RESTORE_ACCEPT_GAP:-}" != 1 ] && "${RUN[@]}" test -e "$PGBACKREST_DIR/state/gap-open"; then
  echo "[restore] the WAL has a hole no backup has closed: a restore to latest stops before it and older data replaces newer. Close it with make backup-now type=diff, or accept that with RESTORE_ACCEPT_GAP=1" >&2
  exit 1
fi
newest_wal="$(python3 "$SCRIPTS/backup_info.py" newest-wal <<< "$info")"

echo "[restore] stopping the app, the sidecar and PostgreSQL"
phase=restoring
"${COMPOSE[@]}" --profile backup stop ${STOP_SERVICES[@]+"${STOP_SERVICES[@]}"} backup postgres
set -- --stanza=main --delta
if [ -n "$TARGET" ]; then
  set -- "$@" --type=time "--target=$TARGET" --target-action=promote
fi
if [ -n "$TIMELINE" ]; then
  set -- "$@" "--target-timeline=$TIMELINE"
fi
"${RUN[@]}" pgbackrest "$@" restore
"${RUN[@]}" touch "$PGBACKREST_DIR/state/stanza-ready"
"${COMPOSE[@]}" up -d --no-deps postgres
# `up --wait` would return at the consistency point, before replay ends. This script
# prints what to do on a failure.
phase=recovering
"$SCRIPTS/backup_restore_wait.sh" "${COMPOSE[@]}"

# Recovery also exits 0 at a missing segment, and a new box has no gap-open marker. The
# segment name without its timeline is the position, which only grows along a branch. The
# branch this recovery followed is the one below the timeline it promoted onto; a max on
# an older branch means the followed one holds no segment yet, and then positions say
# nothing. Read at the preflight, max can only lag: replay past it is normal.
if [ -z "$TARGET" ] && [ -z "$TIMELINE" ] && [ -n "$newest_wal" ]; then
  # shellcheck disable=SC2016  # expanded by the shell inside the container
  position="$("${COMPOSE[@]}" exec -T -u postgres postgres sh -c \
    'psql -XAtq -F " " -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT substr(pg_walfile_name(pg_last_wal_replay_lsn()), 9), substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8)"')"
  read -r replayed timeline <<< "$position"
  followed="$(printf '%08X' "$((16#$timeline - 1))")"
  if [[ ! "${newest_wal:0:8}" < "$followed" ]] && [[ "$replayed" < "${newest_wal:8}" ]]; then
    if [ "${RESTORE_ACCEPT_GAP:-}" = 1 ]; then
      echo "[restore] replay ended at position $replayed, the repository holds up to $newest_wal: the commits past the hole are lost, as RESTORE_ACCEPT_GAP=1 accepts"
    else
      echo "[restore] FAIL: a hole in the WAL: replay ended at position $replayed, the repository holds up to segment $newest_wal, and every commit past the hole is missing from this data. The server stays up on it. A segment under a delete marker can come back (docs/readme/backups.md, A stolen bucket key): then restore with timeline=$((16#${newest_wal:0:8})), because a plain rerun follows the branch this run created and does not see the hole again. To take this data as it is, go on from the check of the data" >&2
      exit 1
    fi
  fi
fi

echo "PostgreSQL runs on the restored data; with BACKUP_ENABLED=true it archives the new timeline."
echo "App, worker, scheduler and the backup sidecar stay stopped."
echo "Next: check the data (make psql). After a bad migration read docs/readme/backups.md before deploying:"
echo "a deploy applies the migrations of the code it ships. Deploy to start the rest of the stack,"
echo "then take a full backup: make backup-now type=full"
