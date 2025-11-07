#!/bin/bash
set -e

# Activate extensions
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    DO \$\$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM pg_settings
            WHERE name = 'shared_preload_libraries'
              AND position('pg_stat_statements' in setting) > 0
        ) THEN
            EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements';
        ELSE
            RAISE NOTICE 'Skipping pg_stat_statements extension; shared_preload_libraries is missing it.';
        END IF;
    END;
    \$\$;

EOSQL
