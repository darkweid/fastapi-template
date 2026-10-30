#!/usr/bin/env bash
# End-to-end check of the backup chain on a local S3 (`make test-backup`, the
# CI backup-smoke job): MinIO with object lock behind TLS, the postgres image
# with backups on, stanza-create, a full backup, rows written after it, and two
# verification restores through scripts/ops/backup_verify.sh - one to the
# latest point, which must return every row, and one to a moment between two
# rows, which must return only the first. Needs Docker and openssl, no secret
# and no network beyond the image registries.
#
# BACKUP_SMOKE_SUFFIX names the compose project and the image tag (CI passes the
# run id); every container, volume, network and image it creates is removed on
# exit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SUFFIX="${BACKUP_SMOKE_SUFFIX:-$$}"
PROJECT="backup-smoke-${SUFFIX}"
WORK_DIR="$(mktemp -d)"

export SMOKE_POSTGRES_IMAGE="backup-smoke-postgres:${SUFFIX}"
export SMOKE_CERTS_DIR="$WORK_DIR/certs"
export SMOKE_BACKUP_ENV_FILE="$WORK_DIR/backup.env"
export SMOKE_S3_KEY="smoke-access-key"
SMOKE_S3_KEY_SECRET="$(openssl rand -hex 16)"
export SMOKE_S3_KEY_SECRET

COMPOSE=(docker compose -p "$PROJECT" -f infra/backup/smoke/docker-compose.yml)

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "[smoke] FAILED; postgres log tail:" >&2
    "${COMPOSE[@]}" logs --tail 50 postgres >&2 2>/dev/null || true
  fi
  "${COMPOSE[@]}" --profile tools down -v --remove-orphans >/dev/null 2>&1 || true
  docker image rm -f "$SMOKE_POSTGRES_IMAGE" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
  exit "$status"
}
trap cleanup EXIT
# A signal becomes an exit, so the EXIT trap removes what was created.
trap 'exit 143' INT TERM

fail() {
  echo "[smoke] FAIL: $*" >&2
  exit 1
}

psql_smoke() {
  "${COMPOSE[@]}" exec -T -u postgres postgres psql -XAtq -v ON_ERROR_STOP=1 -U smoke -d smoke -c "$1"
}

pgbackrest_smoke() {
  "${COMPOSE[@]}" exec -T -u postgres postgres pgbackrest --stanza=main "$@"
}

# MinIO reads public.crt/private.key from its certs directory and wants CAs/
# beside them. Readable by the container user whatever uid it runs as.
mkdir -p "$SMOKE_CERTS_DIR/CAs"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=minio" \
  -keyout "$SMOKE_CERTS_DIR/private.key" -out "$SMOKE_CERTS_DIR/public.crt" 2>/dev/null
chmod 0755 "$WORK_DIR" "$SMOKE_CERTS_DIR" "$SMOKE_CERTS_DIR/CAs"
chmod 0644 "$SMOKE_CERTS_DIR/private.key" "$SMOKE_CERTS_DIR/public.crt"

# The same keys .env.backup.example lists, pointed at MinIO. Path-style
# addressing because "minio" has no per-bucket DNS names; no TLS verification
# because the certificate is self-signed.
cat > "$SMOKE_BACKUP_ENV_FILE" <<EOF
PGBACKREST_REPO1_S3_BUCKET=backups
PGBACKREST_REPO1_S3_ENDPOINT=minio
PGBACKREST_REPO1_S3_REGION=us-east-1
PGBACKREST_REPO1_S3_KEY=${SMOKE_S3_KEY}
PGBACKREST_REPO1_S3_KEY_SECRET=${SMOKE_S3_KEY_SECRET}
PGBACKREST_REPO1_CIPHER_PASS=$(openssl rand -hex 32)
PGBACKREST_REPO1_S3_URI_STYLE=path
PGBACKREST_REPO1_STORAGE_PORT=9000
PGBACKREST_REPO1_STORAGE_VERIFY_TLS=n
EOF

echo "[smoke] building the postgres image"
"${COMPOSE[@]}" build postgres

echo "[smoke] starting MinIO and creating a bucket with object lock"
"${COMPOSE[@]}" up -d --wait minio
"${COMPOSE[@]}" run --rm --no-deps mc --insecure mb --with-lock smoke/backups
"${COMPOSE[@]}" run --rm --no-deps mc --insecure retention set --default COMPLIANCE 1d smoke/backups

echo "[smoke] starting PostgreSQL and creating the stanza"
"${COMPOSE[@]}" up -d --wait postgres
# What deploy.sh does on a box with backups on.
"${COMPOSE[@]}" exec -T -u postgres postgres mkdir -p \
  /var/lib/postgresql/pgbackrest/log \
  /var/lib/postgresql/pgbackrest/spool \
  /var/lib/postgresql/pgbackrest/state
pgbackrest_smoke stanza-create
"${COMPOSE[@]}" exec -T -u postgres postgres touch /var/lib/postgresql/pgbackrest/state/stanza-ready
pgbackrest_smoke check

echo "[smoke] full backup"
"${COMPOSE[@]}" exec -T -u postgres postgres run-backup full

echo "[smoke] writing rows after the backup"
psql_smoke "CREATE TABLE smoke_marker (id int PRIMARY KEY, written_at timestamptz NOT NULL DEFAULT clock_timestamp())"
psql_smoke "INSERT INTO smoke_marker (id) VALUES (1)"
sleep 2
between="$(psql_smoke "SELECT clock_timestamp()")"
sleep 2
psql_smoke "INSERT INTO smoke_marker (id) VALUES (2)"
# check switches the WAL segment and waits until it is in the repository, so
# both inserts are recoverable from here on.
pgbackrest_smoke check

# Stopped, so the live server writes nothing more to the repository: from here
# on the repository may change only if a drill wrote to it.
"${COMPOSE[@]}" stop postgres

# The newest archived segment and the backup labels; a drill that archived
# or backed up would move one of them.
repository_state() {
  "${COMPOSE[@]}" run --rm --no-deps -T -u postgres postgres \
    pgbackrest --stanza=main --output=json info \
    | python3 -c 'import json, sys
stanza = json.load(sys.stdin)[0]
if not stanza["archive"] or not stanza["backup"]:
    sys.exit("pgbackrest info shows no archive or no backup")
print([archive["max"] for archive in stanza["archive"]])
print([backup["label"] for backup in stanza["backup"]])'
}
repository_state > "$WORK_DIR/repository.before"
echo "[smoke] repository before the drills (newest WAL, backups): $(tr '\n' ' ' < "$WORK_DIR/repository.before")"

network="${PROJECT}_default"
verify() {
  POSTGRES_IMAGE="$SMOKE_POSTGRES_IMAGE" \
    BACKUP_ENV_FILE="$SMOKE_BACKUP_ENV_FILE" \
    POSTGRES_USER=smoke \
    POSTGRES_DB=smoke \
    VERIFY_NETWORK="$network" \
    scripts/ops/backup_verify.sh "$@" 2>&1 | tee "$WORK_DIR/verify.out"
}

echo "[smoke] verification restore to the latest point"
verify || fail "the verification restore to the latest point failed"
grep -Eq '^public\.smoke_marker -?[0-9]+ 2$' "$WORK_DIR/verify.out" \
  || fail "the restore to the latest point does not hold both rows"

echo "[smoke] verification restore to ${between}"
verify --target "$between" || fail "the point-in-time restore failed"
grep -Eq '^public\.smoke_marker -?[0-9]+ 1$' "$WORK_DIR/verify.out" \
  || fail "the restore to ${between} does not hold exactly the first row"

echo "[smoke] checking that the drills left the repository alone"
repository_state > "$WORK_DIR/repository.after"
diff "$WORK_DIR/repository.before" "$WORK_DIR/repository.after" \
  || fail "the repository changed during the drills"
# The point-in-time drill promoted onto timeline 2; its history file in the
# repository would mean the drill server archived.
"${COMPOSE[@]}" run --rm --no-deps mc --insecure ls --recursive smoke/backups > "$WORK_DIR/objects"
grep -q 'archive/main/' "$WORK_DIR/objects" || fail "the bucket listing shows no archive"
if grep -q '00000002\.history' "$WORK_DIR/objects"; then
  fail "a drill pushed 00000002.history into the repository"
fi

echo "[smoke] PASS"
