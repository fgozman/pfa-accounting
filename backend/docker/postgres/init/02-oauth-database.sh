#!/bin/bash
# Runs once, on first initialisation of an empty data directory.
#
# Creates the OAuth 2.0 authorization server's OWN database. It holds the
# oauth_* tables only; the application database never does. Owned by the owner
# role (migrations), with CONNECT for the runtime role (DML grants come from the
# migrations in migrations_oauth/).
#
# Existing data directories are handled by `make migrate` (pfa.scripts.migrate),
# which creates the database when it is missing.
set -euo pipefail

APP_USER="${POSTGRES_APP_USER:-pfa_app}"
OAUTH_DB="${POSTGRES_OAUTH_DB:-pfa_oauth}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE ${OAUTH_DB} OWNER ${POSTGRES_USER}'
     WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${OAUTH_DB}')\gexec
    GRANT CONNECT ON DATABASE ${OAUTH_DB} TO ${APP_USER};
EOSQL

echo "[init] OAuth database ${OAUTH_DB} created (runtime role ${APP_USER} may connect)"
