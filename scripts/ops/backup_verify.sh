#!/usr/bin/env bash
# The verification restore: restores the newest backup (or a point in time)
# from the pgBackRest repository into a throwaway volume, starts PostgreSQL on
# it in a throwaway container, and reports what came back. It never names the
# live data volume and never archives, so it is safe on the production box.
#
#   scripts/ops/backup_verify.sh [--target "2026-10-30 14:03:00+00"]
#
# Without --target it fails when the last replayed transaction is older than
# VERIFY_MAX_REPLAY_AGE_SECONDS (two hours by default): the archive check runs
# hourly and archive_timeout is a minute, so an older one means a WAL gap or
# archiving that stopped. A drill to a past moment is old by design and skips
# that check.
#
# Defaults come from the checkout; the end-to-end smoke overrides them:
#   POSTGRES_IMAGE   the image compose builds for the postgres service
#   BACKUP_ENV_FILE  .env.backup
#   POSTGRES_USER, POSTGRES_DB   from .env
#   VERIFY_NETWORK   a docker network to attach to (the default bridge if empty)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

TARGET=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      [ -n "$TARGET" ] || { echo "[verify] --target needs a timestamp" >&2; exit 2; }
      shift 2
      ;;
    --target=*)
      TARGET="${1#--target=}"
      shift
      ;;
    *)
      echo "usage: $0 [--target \"2026-10-30 14:03:00+00\"]" >&2
      exit 2
      ;;
  esac
done

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-.env.backup}"
MAX_AGE="${VERIFY_MAX_REPLAY_AGE_SECONDS:-7200}"
RECOVERY_TIMEOUT="${VERIFY_RECOVERY_TIMEOUT_SECONDS:-21600}"

if [ ! -f "$BACKUP_ENV_FILE" ]; then
  echo "[verify] $BACKUP_ENV_FILE is missing: it holds the repository keys" >&2
  exit 1
fi
if [ -z "${POSTGRES_IMAGE:-}" ]; then
  POSTGRES_IMAGE="$(docker compose --env-file .env -f infra/docker-compose.yml config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["postgres"]["image"])')"
fi
POSTGRES_USER="${POSTGRES_USER:-$(python3 scripts/ops/check_env.py --get POSTGRES_USER)}"
POSTGRES_DB="${POSTGRES_DB:-$(python3 scripts/ops/check_env.py --get POSTGRES_DB)}"
if ! docker image inspect "$POSTGRES_IMAGE" >/dev/null 2>&1; then
  echo "[verify] image $POSTGRES_IMAGE is missing; build it first (make backup-verify does)" >&2
  exit 1
fi

RUN_ID="backup-verify-$(date -u +%Y%m%d%H%M%S)-$$"
VOLUME="$RUN_ID"
CONTAINER="$RUN_ID"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# A signal becomes an exit, so the EXIT trap removes what was created.
trap 'exit 143' INT TERM

docker volume create "$VOLUME" >/dev/null
# A fresh volume belongs to root; the restore runs as postgres.
docker run --rm -u root -v "$VOLUME:/restore" --entrypoint chown "$POSTGRES_IMAGE" postgres:postgres /restore

docker run --rm --name "$CONTAINER" \
  -u postgres \
  --shm-size 256m \
  ${VERIFY_NETWORK:+--network "$VERIFY_NETWORK"} \
  --env-file "$BACKUP_ENV_FILE" \
  -e BACKUP_ENABLED=false \
  -e PGDATA=/restore/data \
  -e PGHOST=/var/run/postgresql \
  -e PGUSER="$POSTGRES_USER" \
  -e PGDATABASE="$POSTGRES_DB" \
  -e RESTORE_TARGET="$TARGET" \
  -e MAX_AGE="$MAX_AGE" \
  -e RECOVERY_TIMEOUT="$RECOVERY_TIMEOUT" \
  -v "$VOLUME:/restore" \
  --entrypoint bash \
  "$POSTGRES_IMAGE" -c "$(cat scripts/ops/backup_verify_in_container.sh)"
