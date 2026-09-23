# Hosted demo on Vercel + Neon (free, always on)

The public demo runs at https://acbi-liard.vercel.app without any PC left switched on.
Vercel serves the React build and runs the FastAPI backend as one serverless function
(`api/index.py`); Neon (PostgreSQL) holds the AdventureWorks copy and the app database.

## How it is wired

| Part | Where | Notes |
| --- | --- | --- |
| Web + API | Vercel project `acbi` (Hobby) | `vercel.json`: builds `frontend/`, rewrites `/api/*` to the function, region `sin1` |
| Warehouse | Neon database `Adventureworks` | role `acbi_ro`, read-only, `statement_timeout=15s` set on the role |
| App data | Neon database `acbi` | role `acbi_app`: users, chats, saved results |
| AI | Gemini, Groq, LiteRouter keys | Vercel environment variables (see below) |

`api/index.py` runs `app.main.startup()` when an instance starts (migrations, dictionary,
dimension members), because serverless does not reliably deliver ASGI lifespan events.
Root `requirements.txt` is what Vercel installs; `pyproject.toml` is excluded in
`.vercelignore` so Vercel does not try to build it as a package.

## Set up from scratch

1. Neon: as the owner role create `acbi_ro` (LOGIN, `default_transaction_read_only=on`,
   `statement_timeout='15s'`), `acbi_app`, databases `"Adventureworks"` and `acbi`
   (owner `acbi_app`).
2. Restore the warehouse dump with `pg_restore --no-owner --no-privileges`, then run the
   `GRANT` block from `hf/entrypoint.sh` in `Adventureworks`.
3. Create the demo users: run `scripts/seed_users.py` with the `APP_DB_*` variables below.
4. Vercel: `vercel link`, add the variables below to `production`, then `vercel deploy --prod`.

Variables (use the Neon *pooled* host for both): `WAREHOUSE_HOST`, `WAREHOUSE_SSLMODE=require`,
`WAREHOUSE_PASSWORD`, `APP_DB_HOST`, `APP_DB_SSLMODE=require`, `APP_DB_PASSWORD`,
`DEMO_LOGIN_ENABLED=true`, `EXTERNAL_METADATA_ENABLED=true`,
`LLM_PROVIDER_ORDER=gemini,groq,literouter`, `GEMINI_API_KEYS`, `LITEROUTER_API_KEY`.

## Limits to know

- Neon free: 0.5 GB storage, the database sleeps after 5 idle minutes; the first request
  after that takes a couple of extra seconds.
- Vercel Hobby is for non-commercial use; a function may run up to 300 s here (a message with several requests can take a while).
- Free AI quotas are shared by everyone using the link.
