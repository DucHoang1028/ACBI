#!/usr/bin/env bash
# Starts PostgreSQL (warehouse copy and application database), the backend and nginx.
# Everything lives on the Space's temporary disk: it is rebuilt on every start.
set -euo pipefail

PGBIN=/usr/lib/postgresql/18/bin
export PGDATA=/home/user/pgdata
DUMP=/home/user/aw.dump

export WAREHOUSE_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
export APP_DB_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
export WAREHOUSE_HOST=127.0.0.1 WAREHOUSE_SSLMODE=disable APP_DB_HOST=127.0.0.1
export FORWARDED_ALLOW_IPS='*'
export EXTERNAL_METADATA_ENABLED="${EXTERNAL_METADATA_ENABLED:-true}"
export DEMO_LOGIN_ENABLED="${DEMO_LOGIN_ENABLED:-true}"
export LLM_PROVIDER_ORDER="${LLM_PROVIDER_ORDER:-gemini,groq,literouter}"

echo "[acbi] fetching the warehouse copy"
if [ -n "${AW_DUMP_URL:-}" ]; then
  curl -fsSL -H "Authorization: Bearer ${HF_TOKEN:-}" "$AW_DUMP_URL" -o "$DUMP"
else
  echo "[acbi] AW_DUMP_URL is not set" >&2; exit 1
fi

echo "[acbi] starting PostgreSQL"
rm -rf "$PGDATA"; mkdir -p "$PGDATA"
"$PGBIN/initdb" -D "$PGDATA" -U postgres --auth-local=trust --auth-host=scram-sha-256 \
  --encoding=UTF8 >/dev/null
"$PGBIN/pg_ctl" -D "$PGDATA" -w -l /tmp/postgres.log \
  -o "-c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp -c fsync=off -c shared_buffers=128MB" start
PSQL=("$PGBIN/psql" -h /tmp -U postgres -X -v ON_ERROR_STOP=1)

"${PSQL[@]}" -d postgres <<SQL
CREATE ROLE acbi_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '$WAREHOUSE_PASSWORD';
ALTER ROLE acbi_ro SET default_transaction_read_only = on;
ALTER ROLE acbi_ro SET statement_timeout = '15s';
CREATE ROLE acbi_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$APP_DB_PASSWORD';
CREATE DATABASE "Adventureworks";
CREATE DATABASE acbi OWNER acbi_app;
SQL

echo "[acbi] restoring the warehouse copy"
"$PGBIN/pg_restore" -h /tmp -U postgres -d Adventureworks --no-owner --no-privileges \
  --exit-on-error "$DUMP" || echo "[acbi] restore reported warnings" >&2
rm -f "$DUMP"

"${PSQL[@]}" -d Adventureworks <<'SQL'
GRANT CONNECT ON DATABASE "Adventureworks" TO acbi_ro;
GRANT USAGE ON SCHEMA production, sales, acbi_demo TO acbi_ro;
GRANT SELECT ON production.workorder, production.workorderrouting,
 production.location, production.product, production.productsubcategory,
 production.productcategory, production.scrapreason,
 sales.salesorderheader, sales.salesorderdetail, sales.salesterritory TO acbi_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA acbi_demo TO acbi_ro;
SQL

echo "[acbi] starting the backend"
cd /home/user/app
"${PYTHON:-python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers &
BACKEND=$!
for _ in $(seq 1 90); do
  curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
  kill -0 "$BACKEND" 2>/dev/null || { echo "[acbi] backend stopped" >&2; exit 1; }
  sleep 1
done
python scripts/seed_users.py

echo "[acbi] ready on port ${PORT:-7860}"
exec nginx -c /home/user/app/nginx.conf -g 'daemon off;'
