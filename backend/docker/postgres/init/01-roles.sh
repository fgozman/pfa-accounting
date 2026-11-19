#!/bin/bash
# Runs once, on first initialisation of an empty data directory.
#
# Creates the RUNTIME role. The owner role (created by the postgres image from
# POSTGRES_USER) owns the schema and runs migrations. The runtime role is used
# by the API and workers: it is NOT superuser, does NOT own tables and does NOT
# have BYPASSRLS, so row-level security applies to it.
set -euo pipefail

APP_USER="${POSTGRES_APP_USER:-pfa_app}"
APP_PASSWORD="${POSTGRES_APP_PASSWORD:-dev_app_password}"
DB_NAME="${POSTGRES_DB:-pfa}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB_NAME" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
            CREATE ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}'
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
        END IF;
    END
    \$\$;
    GRANT CONNECT ON DATABASE ${DB_NAME} TO ${APP_USER};
EOSQL

echo "[init] runtime role ${APP_USER} created for database ${DB_NAME}"
