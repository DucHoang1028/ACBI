"""Explicit admin-only Phase 0 setup, using Docker's local psql client.

Keeps the source folder untouched. Credentials are generated into deploy/.env,
which is excluded from Git. No application process uses the administrator role.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    container = os.environ.get(
        "WAREHOUSE_CONTAINER", "adventureworks-for-postgres-db-1"
    )
    database = os.environ.get("WAREHOUSE_DATABASE", "Adventureworks")
    env_file = ROOT / "deploy/.env"
    if not env_file.exists():
        template = (ROOT / "deploy/.env.example").read_text(encoding="utf-8")
        template = template.replace(
            "CHANGE_WAREHOUSE_PASSWORD", secrets.token_urlsafe(32)
        )
        template = template.replace("CHANGE_APP_PASSWORD", secrets.token_urlsafe(32))
        env_file.write_text(template, encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    password = values["WAREHOUSE_PASSWORD"].replace("'", "''")
    # Password goes through stdin, never command-line arguments or logs.
    sql = r"""
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='acbi_ro') THEN
   CREATE ROLE acbi_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE acbi_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE acbi_ro PASSWORD '__PASSWORD__';
ALTER ROLE acbi_ro SET default_transaction_read_only = on;
ALTER ROLE acbi_ro SET statement_timeout = '15s';
GRANT CONNECT ON DATABASE "Adventureworks" TO acbi_ro;
GRANT USAGE ON SCHEMA production, sales TO acbi_ro;
GRANT SELECT ON production.workorder, production.workorderrouting,
 production.location, production.product, production.productsubcategory,
 production.productcategory, production.scrapreason,
 sales.salesorderheader, sales.salesorderdetail, sales.salesterritory TO acbi_ro;
COMMIT;
""".replace("__PASSWORD__", password)
    if database != "Adventureworks":
        raise ValueError("Verify and update the database-specific grant before setup")
    sql += (ROOT / "data/warehouse_extensions.sql").read_text(encoding="utf-8")
    command = [
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        "postgres",
        "-d",
        database,
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    result = subprocess.run(command, input=sql, text=True, capture_output=True)
    if result.returncode:
        # psql can echo failing SQL containing credentials. Do not forward it.
        raise RuntimeError("Warehouse setup failed; inspect with an administrator")
    print("Read-only role and synthetic factory extension configured.")


if __name__ == "__main__":
    main()
