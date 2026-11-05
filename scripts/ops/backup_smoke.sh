#!/usr/bin/env bash
# End-to-end check of the backup chain on a local S3 (`make test-backup`, the
# CI backup-smoke job): MinIO with object lock behind TLS, the postgres image
# with backups on, stanza-create, a full backup taken from a second container
# on the server's data and socket volumes (the sidecar's shape), rows written
# after it, and two verification restores through scripts/ops/backup_verify.sh -
# one to the latest point, which must return every row, and one to a moment
# between two rows, which must return only the first. Last, the live restore of
# `make backup-restore` onto a fresh volume, as on a replacement box: it must
# return every row and ship the new timeline's history file to the repository.
# Before the rows, an S3 outage: the server must not restart, a long session must
# survive, and archiving must recover when the bucket returns. After the restore,
# what must fail: a target past a fork, a hole in the WAL, an open gap, a hidden
# manifest and a damaged object; and an expire under Object Lock, which leaves delete
# markers over locked versions and a repository the drill still restores.
# Needs Docker and openssl, no secret and no network beyond the image registries.
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

echo "[smoke] full backup from the sidecar-shaped container"
"${COMPOSE[@]}" run --rm --no-deps -T backup run-backup full
"${COMPOSE[@]}" run --rm --no-deps -T backup check-archive
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure ls --recursive smoke/backups > "$WORK_DIR/objects"
grep -q 'pgbackrest/backup/main/.*/bundle/' "$WORK_DIR/objects" \
  || fail "the full backup left no bundle/ object: bundling is off"
"${COMPOSE[@]}" run --rm --no-deps -T backup pgrep -V >/dev/null \
  || fail "the image has no pgrep: a drill cannot see a running expire"

# A crash of the cluster, from the postmaster's log. Nothing else prints these; an
# untracked child that exits 0 is harmless, so that line alone does not count.
crash_lines() {
  "${COMPOSE[@]}" logs --no-log-prefix postgres 2>/dev/null \
    | grep -cE 'all server processes terminated; reinitializing|terminating any other active' || true
}

echo "[smoke] S3 outage: the server must keep running"
[ "$(crash_lines)" = "0" ] || fail "the cluster crashed before the outage"
"${COMPOSE[@]}" stop minio
psql_smoke "SELECT pg_sleep(45)" >/dev/null &
long_session=$!
psql_smoke "CREATE TABLE smoke_filler (body text)"
psql_smoke "INSERT INTO smoke_filler SELECT repeat(md5(i::text), 10) FROM generate_series(1, 1000) i"
# archive_timeout closes a segment a minute at the latest; do not wait for it.
psql_smoke "SELECT pg_switch_wal()" >/dev/null
sleep 30
[ "$(crash_lines)" = "0" ] || fail "the S3 outage crashed the cluster"
wait "$long_session" || fail "a long session did not survive the S3 outage"

echo "[smoke] the bucket is back: archiving must recover"
"${COMPOSE[@]}" up -d --wait minio
# Until the queued segments are gone, check waits on them and the switch.
for attempt in 1 2 3; do
  pgbackrest_smoke check && break
  [ "$attempt" -lt 3 ] || fail "archiving did not recover after the bucket came back"
  sleep 20
done

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
    VERIFY_LIVE_CONTAINER="$("${COMPOSE[@]}" ps -q postgres)" \
    VERIFY_SIDECAR_CONTAINER="" \
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

# The script of `make backup-restore` on a volume that never held a cluster, then the
# check that the promoted server archives its new timeline. This stack has no app.
restore() {
  CONFIRM=1 RESTORE_STOP_SERVICES="" scripts/ops/backup_restore.sh "${COMPOSE[@]}"
}

echo "[smoke] live restore onto a fresh volume"
"${COMPOSE[@]}" rm -sf postgres
docker volume rm "${PROJECT}_postgres-data" >/dev/null
restore || fail "the live restore did not finish"
[ "$(psql_smoke "SELECT count(*) FROM smoke_marker")" = "2" ] \
  || fail "the live restore does not hold both rows"
pgbackrest_smoke check
"${COMPOSE[@]}" run --rm --no-deps mc --insecure ls --recursive smoke/backups > "$WORK_DIR/objects"
grep -q '00000002\.history' "$WORK_DIR/objects" \
  || fail "the restored server did not archive 00000002.history"
"${COMPOSE[@]}" run --rm --no-deps -T backup run-backup full

echo "[smoke] a target past a fork: drill and restore refuse, a named timeline gets there"
psql_smoke "CREATE TABLE smoke_fork (id int PRIMARY KEY)"
psql_smoke "INSERT INTO smoke_fork VALUES (1)"
sleep 2
fork_first="$(psql_smoke "SELECT clock_timestamp()")"
sleep 2
psql_smoke "INSERT INTO smoke_fork VALUES (2)"
sleep 2
fork_second="$(psql_smoke "SELECT clock_timestamp()")"
sleep 2
psql_smoke "INSERT INTO smoke_fork VALUES (3)"
pgbackrest_smoke check
RESTORE_TARGET="$fork_first" restore || fail "the restore to the first moment did not finish"
[ "$(psql_smoke "SELECT count(*) FROM smoke_fork")" = "1" ] || fail "the restore to the first moment holds the wrong rows"
psql_smoke "INSERT INTO smoke_fork VALUES (100)"
pgbackrest_smoke check
sleep 2
on_branch="$(psql_smoke "SELECT clock_timestamp()")"
sleep 2
psql_smoke "INSERT INTO smoke_fork VALUES (101)"
pgbackrest_smoke check
echo "[smoke] a target on the branch itself is not refused"
verify --target "$on_branch" || fail "the drill to a target on the branch failed"
grep -Eq '^public\.smoke_fork -?[0-9]+ 2$' "$WORK_DIR/verify.out" \
  || fail "the drill to a target on the branch does not hold the branch's rows"
if verify --target "$fork_second"; then
  fail "the drill to a target past a fork printed OK"
fi
grep -q 'a restore left timeline' "$WORK_DIR/verify.out" || fail "the drill failed for another reason than the fork"
if RESTORE_TARGET="$fork_second" restore 2> "$WORK_DIR/refused"; then
  fail "a restore to a target past a fork was not refused"
fi
grep -q 'a restore left timeline' "$WORK_DIR/refused" || fail "the refusal does not name the fork"
[ "$(psql_smoke "SELECT string_agg(id::text, ',' ORDER BY id) FROM smoke_fork")" = "1,100,101" ] \
  || fail "the refused restore touched the live server"
parent_timeline="$((16#$(psql_smoke "SELECT substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8)") - 1))"
verify --target "$fork_second" --timeline "$parent_timeline" || fail "the drill on the named timeline failed"
grep -Eq '^public\.smoke_fork -?[0-9]+ 2$' "$WORK_DIR/verify.out" \
  || fail "the drill on the named timeline does not hold the parent's rows"

echo "[smoke] expire under Object Lock: delete markers over locked versions, a drill after"
oldest_backup="$(pgbackrest_smoke --output=json info | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["backup"][0]["label"])')"
# Time-based retention counts whole days and the smoke cannot wait one; the count type
# drives the same removal against the same locked bucket.
retain_one=(-e PGBACKREST_REPO1_RETENTION_FULL_TYPE=count -e PGBACKREST_REPO1_RETENTION_FULL=1)
"${COMPOSE[@]}" run --rm --no-deps -T "${retain_one[@]}" backup run-backup diff
pgbackrest_smoke --output=json info > "$WORK_DIR/kept"
grep -q "$oldest_backup" "$WORK_DIR/kept" || fail "a backup expired on its own: expire-auto is on"
# The drill below wants a transaction to replay after that backup.
psql_smoke "CREATE TABLE smoke_after_diff (id int PRIMARY KEY)"
pgbackrest_smoke check
# One attempt at any hour: a broken expire must fail here, not retry into the CI timeout.
"${COMPOSE[@]}" run --rm --no-deps -T "${retain_one[@]}" -e EXPIRE_BUDGET_SECONDS=0 -e EXPIRE_STOP_HOUR=24 backup run-expire \
  || fail "the expire under Object Lock failed"
pgbackrest_smoke --output=json info > "$WORK_DIR/expired"
if grep -q "$oldest_backup" "$WORK_DIR/expired"; then
  fail "expire left the oldest backup $oldest_backup"
fi
expired_path="smoke/backups/pgbackrest/backup/main/$oldest_backup"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure --json ls --recursive --versions "$expired_path/" > "$WORK_DIR/versions"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure ls --recursive "$expired_path/" > "$WORK_DIR/current"
if [ -s "$WORK_DIR/current" ]; then
  cat "$WORK_DIR/current" >&2
  fail "expire left objects of $oldest_backup visible"
fi
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure --json ls --recursive --versions smoke/backups/pgbackrest/archive/main/ > "$WORK_DIR/archive-versions"
grep -q '"isDeleteMarker":true' "$WORK_DIR/archive-versions" \
  || fail "expire removed no WAL: the archive holds no delete marker"
# Prints the version id and the key of a file expire hid; the version must still be there.
locked_version="$(python3 -c 'import json, sys
entries = [json.loads(line) for line in open(sys.argv[1])]
if not any(entry.get("isDeleteMarker") for entry in entries):
    sys.exit("expire left no delete marker in the backup")
print(*next((entry["versionId"], entry["key"]) for entry in entries if not entry.get("isDeleteMarker")))' "$WORK_DIR/versions")"
if "${COMPOSE[@]}" run --rm --no-deps -T mc --insecure rm --version-id "${locked_version% *}" "$expired_path/${locked_version#* }"; then
  fail "a locked version expire hid could be deleted"
fi
verify || fail "the drill after expire failed"
grep -Eq '^public\.smoke_marker -?[0-9]+ 2$' "$WORK_DIR/verify.out" \
  || fail "the drill after expire does not hold the rows"
grep -q "backup '$oldest_backup' found in the repository" "$WORK_DIR/verify.out" \
  || fail "the drill after expire never met the backup expire removed"

echo "[smoke] a hole in the middle of the WAL fails the drill, whatever the age of the data"
psql_smoke "CREATE TABLE smoke_hole (id int PRIMARY KEY)"
psql_smoke "INSERT INTO smoke_hole VALUES (1)"
pgbackrest_smoke check
psql_smoke "INSERT INTO smoke_hole VALUES (2)"
hidden_segment="$(psql_smoke "SELECT pg_walfile_name(pg_current_wal_insert_lsn())")"
pgbackrest_smoke check
psql_smoke "INSERT INTO smoke_hole VALUES (3)"
pgbackrest_smoke check
psql_smoke "INSERT INTO smoke_hole VALUES (4)"
pgbackrest_smoke check
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure ls --recursive smoke/backups > "$WORK_DIR/objects"
hidden="$(grep -m1 -oE "pgbackrest/archive/main/.*/${hidden_segment}-[^ ]*" "$WORK_DIR/objects" || true)"
[ -n "$hidden" ] || fail "no archived object for segment $hidden_segment"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure rm "smoke/backups/$hidden"
if verify; then
  fail "the drill printed OK over a hole in the WAL"
fi
grep -q 'a hole in the WAL' "$WORK_DIR/verify.out" || fail "the drill failed for another reason than the hole"

echo "[smoke] an open gap fails the drill before it restores anything"
"${COMPOSE[@]}" exec -T -u postgres postgres touch /var/lib/postgresql/pgbackrest/state/gap-open
if verify; then
  fail "the drill printed OK with a gap open"
fi
grep -q 'a gap is open' "$WORK_DIR/verify.out" || fail "the drill failed for another reason than the gap"
"${COMPOSE[@]}" exec -T -u postgres postgres rm /var/lib/postgresql/pgbackrest/state/gap-open

echo "[smoke] a restore to latest over the hole on an empty volume fails: the marker went with the old volume"
"${COMPOSE[@]}" rm -sf postgres
docker volume rm "${PROJECT}_postgres-data" >/dev/null
if restore 2> "$WORK_DIR/hole"; then
  fail "a restore to latest over a hole in the WAL finished"
fi
grep -q 'FAIL: a hole in the WAL' "$WORK_DIR/hole" || { cat "$WORK_DIR/hole" >&2; fail "the restore failed for another reason than the hole"; }
[ "$(psql_smoke "SELECT string_agg(id::text, ',' ORDER BY id) FROM smoke_hole")" = "1" ] \
  || fail "the failed restore did not leave the server on the data before the hole"

echo "[smoke] with the segment back, the branch below the failed restore holds every row"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure --json ls --recursive --versions "smoke/backups/$hidden" > "$WORK_DIR/hole-versions"
hole_marker="$(python3 -c 'import json, sys
print(next(entry["versionId"] for entry in map(json.loads, open(sys.argv[1])) if entry.get("isDeleteMarker")))' "$WORK_DIR/hole-versions")"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure rm --version-id "$hole_marker" "smoke/backups/$hidden"
hole_history="$(psql_smoke "SELECT pg_read_file('pg_wal/' || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8) || '.history')")"
RESTORE_TIMELINE="$(awk 'END {print $1}' <<< "$hole_history")" restore \
  || fail "the restore of the branch below the failed one did not finish"
[ "$(psql_smoke "SELECT string_agg(id::text, ',' ORDER BY id) FROM smoke_hole")" = "1,2,3,4" ] \
  || fail "the restore of the branch below the failed one did not bring back the rows past the hole"

echo "[smoke] a hidden manifest fails the drill"
"${COMPOSE[@]}" run --rm --no-deps -T backup run-backup full
newest_backup="$(pgbackrest_smoke --output=json info | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["backup"][-1]["label"])')"
"${COMPOSE[@]}" run --rm --no-deps -T backup run-backup full
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure rm \
  "smoke/backups/pgbackrest/backup/main/$newest_backup/backup.manifest" \
  "smoke/backups/pgbackrest/backup/main/$newest_backup/backup.manifest.copy"
if verify; then
  fail "the drill printed OK with a manifest missing"
fi
grep -q 'a backup that cannot be restored' "$WORK_DIR/verify.out" || fail "the drill failed for another reason than the manifest"

echo "[smoke] a damaged repository object fails the drill"
"${COMPOSE[@]}" stop postgres
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure ls --recursive smoke/backups > "$WORK_DIR/objects"
# Bundling leaves small files in bundle/; only one past the bundle limit gets its own object.
damaged="$(grep -m1 -oE 'pgbackrest/backup/main/.*/(bundle|pg_data)/[^ ]*' "$WORK_DIR/objects" || true)"
[ -n "$damaged" ] || fail "the bucket listing shows no backup file to damage"
# Same size, bytes flipped halfway: a shorter bundle costs every file in it a range error
# and its HTTP retries, about 50 minutes for one bundle.
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure cat "smoke/backups/$damaged" > "$WORK_DIR/object"
python3 -c 'import sys
data = bytearray(open(sys.argv[1], "rb").read())
middle = len(data) // 2
data[middle:middle + 64] = bytes(byte ^ 0xFF for byte in data[middle:middle + 64])
open(sys.argv[1], "wb").write(data)' "$WORK_DIR/object"
"${COMPOSE[@]}" run --rm --no-deps -T mc --insecure pipe "smoke/backups/$damaged" < "$WORK_DIR/object"
if verify; then
  fail "the drill passed over a damaged repository object"
fi
grep -q 'the repository holds damaged objects' "$WORK_DIR/verify.out" \
  || fail "the repository check let a damaged object through"

echo "[smoke] PASS"
