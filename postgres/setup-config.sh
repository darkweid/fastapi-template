#!/bin/bash
set -euo pipefail

if [[ -z "${PGDATA:-}" ]]; then
    echo "PGDATA is not defined; cannot apply custom configuration." >&2
    exit 1
fi

echo "Applying custom postgresql.conf to ${PGDATA}"

# Stop the temporary server that docker-entrypoint started for initialization
pg_ctl -D "$PGDATA" -m fast -w stop

cp /custom-config/postgresql.conf "$PGDATA/postgresql.conf"
chown postgres:postgres "$PGDATA/postgresql.conf"
chmod 0644 "$PGDATA/postgresql.conf"

# Restart the server so subsequent init scripts see the custom settings
pg_ctl -D "$PGDATA" -o "-c listen_addresses='localhost'" -w start
