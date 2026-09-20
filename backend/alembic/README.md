# Schema management

The application database is created and upgraded by idempotent `migrate()` functions
(`auth`, `conversation`, `history`) that run at startup with `CREATE TABLE IF NOT EXISTS`
and `ADD COLUMN IF NOT EXISTS`. The proposal fixes no migration tool, so Alembic is not
wired in; this directory is kept for it. If you adopt Alembic, make the first revision
call those same `migrate()` functions so there is one source of truth.
