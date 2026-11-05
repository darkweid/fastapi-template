#!/usr/bin/env bash
# The half of scripts/ops/backup_verify.sh that runs inside the throwaway
# postgres container, as postgres, with the scratch volume at /restore. It reads
# RESTORE_TARGET, RESTORE_TIMELINE, MAX_AGE, RECOVERY_TIMEOUT and LIVE_ARCHIVED_WAL from
# the environment.
set -euo pipefail

mkdir -m 0700 /restore/data
# The image ships no log directory (deploy.sh makes it on the live volume), and
# pgBackRest warns on every command that cannot open its log file.
mkdir -p /var/lib/postgresql/pgbackrest/log
# --archive-mode=off writes archive_mode=off into the restored cluster, so the
# drill server never pushes WAL into the live stanza; BACKUP_ENABLED=false on
# the container makes the archive wrapper a no-op as a second guard. The rest of
# the restore options, process-max among them, come from pgbackrest.conf.
set -- --stanza=main --pg1-path=/restore/data --archive-mode=off
if [ -n "${RESTORE_TARGET:-}" ]; then
  set -- "$@" --type=time "--target=$RESTORE_TARGET" --target-action=promote
fi
if [ -n "${RESTORE_TIMELINE:-}" ]; then
  set -- "$@" "--target-timeline=$RESTORE_TIMELINE"
fi
# Every object against its checksum, older backups and WAL too; the restore below proves
# only the backup it uses. It exits 0 over a damaged object and over a missing manifest
# (seen on 2.60), so the verdict is in the text: only --verbose lists every backup,
# whatever its state.
echo "[verify] checking the repository"
pgbackrest --stanza=main --verbose verify 2>&1 | tee /tmp/repository-check
if ! grep -Eq '^[[:space:]]*status: ok$' /tmp/repository-check; then
  echo "[verify] FAIL: the repository holds damaged objects (listed above)" >&2
  exit 1
fi
# A backup expire removed under Object Lock, every object under a delete marker, reads as
# a missing manifest on MinIO; backup.info no longer lists it and nothing restores from it.
unusable="$(grep -E '^[[:space:]]*backup: .*, status: ' /tmp/repository-check | grep -Ev ', status: (valid|in progress),' || true)"
expired="$(sed -nE "s/.*WARN: backup '([^']+)' found in the repository but not in backup\.info.*/\1/p" /tmp/repository-check | paste -sd '|' -)"
if [ -n "$expired" ]; then
  unusable="$(printf '%s\n' "$unusable" | grep -Ev "backup: ($expired)," || true)"
fi
if [ -n "$unusable" ]; then
  printf '%s\n' "$unusable"
  echo "[verify] FAIL: the repository holds a backup that cannot be restored (listed above)" >&2
  exit 1
fi

echo "[verify] restoring"
pgbackrest "$@" restore

echo "[verify] starting the server and replaying WAL"
# A small fixed shared_buffers whatever the box's profile sizes the live server
# to, so the drill fits beside it; the unix socket only.
postgres -D /restore/data -c config_file=/etc/postgresql/postgresql.conf \
  -c shared_buffers=256MB -c listen_addresses= &
server=$!
deadline=$((SECONDS + RECOVERY_TIMEOUT))
until [ "$(psql -XAtq -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" = "f" ]; do
  if ! kill -0 "$server" 2>/dev/null; then
    echo "[verify] FAIL: the server stopped during recovery (its log is above)" >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[verify] FAIL: recovery did not finish within ${RECOVERY_TIMEOUT}s" >&2
    exit 1
  fi
  sleep 2
done
echo "[verify] recovery finished, the server accepts writes"

replayed="$(psql -XAtq -c "SELECT coalesce(pg_last_xact_replay_timestamp()::text, 'none')")"
age="$(psql -XAtq -c "SELECT coalesce(extract(epoch FROM now() - pg_last_xact_replay_timestamp())::bigint::text, 'none')")"
echo "[verify] last replayed transaction: $replayed (age: ${age}s)"

replay_segment="$(psql -XAtq -c "SELECT substr(pg_walfile_name(pg_last_wal_replay_lsn()), 9)")"

echo "[verify] largest tables (name, planner estimate, count):"
# reltuples, not n_live_tup: the statistics collector starts empty after recovery.
psql -XAtq -F ' ' <<'SQL'
SELECT format(
  'SELECT %L, %s, count(*) FROM %I.%I',
  n.nspname || '.' || c.relname, c.reltuples::bigint, n.nspname, c.relname
)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY c.reltuples DESC, n.nspname, c.relname
LIMIT 5
\gexec
SQL

pg_ctl -D /restore/data -m fast -w stop >/dev/null

# The name without its timeline: the position in WAL, which only grows along a branch. A
# named branch can end earlier by design.
if [ -z "${RESTORE_TARGET:-}" ] && [ -z "${RESTORE_TIMELINE:-}" ] && [ -n "${LIVE_ARCHIVED_WAL:-}" ] \
  && [[ "$replay_segment" < "${LIVE_ARCHIVED_WAL:8}" ]]; then
  echo "[verify] FAIL: a hole in the WAL: replay ended at segment $replay_segment, the live server archived up to $LIVE_ARCHIVED_WAL. A restore would stop there too. Close it with make backup-now type=diff" >&2
  exit 1
fi
if [ -z "${RESTORE_TARGET:-}" ]; then
  if [ "$age" = "none" ]; then
    echo "[verify] FAIL: no transaction was replayed after the backup: either nothing was committed since it, or that WAL never reached the repository (see docs/readme/backups.md)" >&2
    exit 1
  fi
  if [ "$age" -gt "$MAX_AGE" ]; then
    echo "[verify] FAIL: the newest recoverable transaction is ${age}s old (limit ${MAX_AGE}s): a WAL gap or archiving that stopped" >&2
    exit 1
  fi
fi
echo "[verify] OK"
