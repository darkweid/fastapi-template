#!/usr/bin/env bash
# Waits for the postgres service of a live restore (`make backup-restore`) to
# finish replaying WAL and leave recovery. Its healthcheck turns green at the
# consistency point, long before that, and a target past the end of the WAL
# makes the server exit FATAL and come back under `restart: always`, so this
# polls pg_is_in_recovery() and fails on a restart.
#
#   scripts/ops/backup_restore_wait.sh docker compose --env-file .env -f ...
#
# RESTORE_RECOVERY_TIMEOUT_SECONDS bounds the wait (six hours by default).
set -euo pipefail

[ "$#" -gt 0 ] || { echo "usage: $0 <compose command>" >&2; exit 2; }
COMPOSE=("$@")
TIMEOUT="${RESTORE_RECOVERY_TIMEOUT_SECONDS:-21600}"

fail() {
  echo "[restore] FAIL: $*; the postgres log tail:" >&2
  "${COMPOSE[@]}" logs --tail 50 postgres >&2 || true
  exit 1
}

container="$("${COMPOSE[@]}" ps -q postgres)"
[ -n "$container" ] || fail "the postgres container is not running"
restarts="$(docker inspect -f '{{.RestartCount}}' "$container")"
deadline=$((SECONDS + TIMEOUT))
echo "[restore] waiting for PostgreSQL to replay WAL and leave recovery"
while true; do
  # shellcheck disable=SC2016  # expanded by the shell inside the container
  state="$("${COMPOSE[@]}" exec -T postgres sh -c \
    'psql -XAtq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_is_in_recovery()"' 2>/dev/null || true)"
  [ "$state" = "f" ] && break
  now="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$container")"
  if [ "$now" != "running $restarts" ]; then
    fail "the server stopped during recovery ($now); a target past the end of the archived WAL does this"
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    fail "recovery did not finish within ${TIMEOUT}s"
  fi
  sleep 5
done
echo "[restore] recovery finished, the server accepts writes"
