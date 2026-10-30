#!/usr/bin/env bash
# The half of scripts/ops/backup_verify.sh that runs inside the throwaway
# postgres container, as postgres, with the scratch volume at /restore. It reads
# RESTORE_TARGET, MAX_AGE and RECOVERY_TIMEOUT from the environment.
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

if [ -z "${RESTORE_TARGET:-}" ]; then
  if [ "$age" = "none" ]; then
    echo "[verify] FAIL: no transaction was replayed after the backup: WAL written since never reached the repository" >&2
    exit 1
  fi
  if [ "$age" -gt "$MAX_AGE" ]; then
    echo "[verify] FAIL: the newest recoverable transaction is ${age}s old (limit ${MAX_AGE}s): a WAL gap or archiving that stopped" >&2
    exit 1
  fi
fi
echo "[verify] OK"
