# Local ACBI runbook

Run from `C:/Users/ADMIN/Desktop/ACBI Project`. Docker Desktop must be running, and the pre-existing AdventureWorks container must remain available. Never run setup commands inside its reference folder.

## Start and stop

PowerShell:

```powershell
./scripts/acbi.ps1 up
./scripts/acbi.ps1 test
./scripts/acbi.ps1 eval
./scripts/acbi.ps1 seed-users
./scripts/acbi.ps1 verify-users
./scripts/acbi.ps1 verify-chat
./scripts/acbi.ps1 verify-rag
./scripts/acbi.ps1 verify-phase4
./scripts/acbi.ps1 down
```

With Make installed: `make up`, `make test PYTHON=.tools/acbi-env/Scripts/python.exe`, `make eval`, `make down`. The Windows script's test/seed commands use the isolated environment installed during this task. To recreate it, use Python 3.12 to create `.tools/acbi-env` and install `backend/requirements-dev.txt`.

Open http://localhost:8080. The only published ACBI port is loopback 8080. The app database does not publish 5432. Stop retains the named database volume; do not use `down -v` unless deliberately deleting application state.

The default local wrapper includes `deploy/docker-compose.local.yml` because Windows has another PostgreSQL service on port 5432. The override attaches only the ACBI backend to AdventureWorks's existing network and uses the warehouse container's unique DNS name. `acbi-app-db` uniquely identifies the internal application database.

On a host without that conflict, omit the override: `make up COMPOSE_EXTRA=` or use `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build --wait`. The base configuration uses the requested host.docker.internal:5432. Set warehouse host, database, credentials and TLS appropriately.

## Administrative setup

`scripts/prepare_warehouse.py` is an explicitly administrative tool. It uses the existing container's psql as its administrator to create/configure acbi_ro and execute `data/warehouse_extensions.sql`. It creates random local passwords in gitignored `deploy/.env` only when that file does not exist. Re-running uses the same password and preserves existing synthetic mappings. Application code only connects to the warehouse as acbi_ro; it rejects other usernames.

The database name was verified with `psql -U postgres -d postgres -c '\l'`. The only supported prepared name is Adventureworks; the script intentionally stops if another name is selected without updating its reviewed grant.

The extension is idempotent for this schema version, not a general migration engine. Review changes to existing extension structures before rerunning a changed version. Do not change original workorder or sales tables to fabricate missing generated columns.

## Verification and records

The evaluation script runs inside the backend so it uses the same network and acbi_ro credentials as the app. It writes reports into a temporary container directory, then the wrapper copies them to:

- `docs/phase0-verification.json`: date ranges, relevant row counts, role settings, security-check outcomes and definition/question hashes.
- `data/eval/results.local.json`: complete local reference results with SQL, bound parameters, versions and anchor. This file is gitignored.

The Phase 0 reference runner distinguishes executed SQL checks from denied/ambiguous question contracts. Phase 2's `verify-chat` uses FakeLLM, authenticated users and the live read-only warehouse to check all 40 golden cases, including denied and ambiguous outcomes. It writes `docs/phase2-verification.json` without credentials or business rows.

`DATA_AS_OF` may be set in deploy/.env. Restart/recreate the backend after changing it. Out-of-range dates prevent startup. “Recently” is deliberately unresolved until the user clarifies its duration.

`verify-rag` checks six generated-query cases, chart validation/fallback, and SQL safety with FakeLLM against the read-only warehouse. It writes `docs/phase3-verification.json`. No provider call is made.

`verify-phase4` checks saved-result ownership and provenance, voice review with FakeSTT, administrator boundaries and user management, five concurrent FakeLLM requests, and controlled failure outcomes. It writes `docs/phase4-verification.json` without credentials or business rows. It creates and removes one temporary test account. It does not send audio or business data to Groq.

No Groq key is needed for offline verification. A server-side `GROQ_API_KEY` enables live questions. Keep the key out of frontend configuration and source control. The backend defaults to `openai/gpt-oss-120b` with strict structured JSON. The local provider budget starts at 15 requests/minute, 8,000 estimated tokens/minute, and at most three calls per question including retries. Provider errors and exhausted local budget return a technical-failure response. The owner approved the [documented external context](PHASE_3_EXTERNAL_CONTEXT.md), so the local private environment now sets `EXTERNAL_METADATA_ENABLED=true`. Keep `SEND_RESULTS_TO_LLM=false` and `EXTERNAL_RESULTS_ENABLED=false` unless result-row export receives separate approval.

## Phase 1 accounts

Once the three services are healthy, run `./scripts/acbi.ps1 seed-users`. It creates manager, sales, production_a and it_admin only if missing. Passwords are generated randomly, hashed with Argon2 in the app DB, and copied once to gitignored `deploy/seed-credentials.txt`; they are not printed. Keep this local file private. Re-running does not overwrite existing accounts or rotate passwords. This seed tool never connects to the external warehouse as an administrator.

`./scripts/acbi.ps1 verify-users` uses the private file to exercise login and role checks inside the backend, then copies a credential-free report to `docs/phase1-verification.json`. It remains usable after containers are recreated.

The access token stays in browser memory; refresh rotates an HttpOnly cookie. The web service is loopback-only HTTP for local development. Before deploying externally, terminate HTTPS and configure secure cookie handling; do not expose the current local profile as a production login service.

## Phase 2 questions

Sign in at http://localhost:8080, ask about an approved metric, and inspect the result table and source details. The UI shows the data anchor. A follow-up question can reuse the previous conversation's metric, period and filter slots; use **New question** to start fresh. “Recently” and questions without a clear metric or period request clarification. Production users are fixed to Factory A, sales users cannot request production or quality data, and it_admin cannot request business data. The warehouse query is chosen from static templates and uses bound values. It is never generated by Groq.

Successful results and no-data results are saved with their original source date anchor and can be reopened from **Saved results**. A current role or Factory A scope change can block reopening. Browser voice capture sends audio to Groq transcription only after **Stop and transcribe**, then shows editable transcript text. Administrator accounts use the administration screen to view users, roles, scopes and denial audit, create a user, change a role or enable/disable an account. Changing a role revokes existing sessions. Phase 4 is locally verified; the [report](PHASE_4_REPORT.md) distinguishes FakeLLM results from one live Groq smoke test.

## Groq keys and live evaluation
Put the primary key in `GROQ_API_KEY` and any extra keys, comma-separated, in `GROQ_API_KEYS` in `deploy/.env` (never commit it). The backend uses one key at a time; a 429, 5xx, transport error or invalid key moves the request to the next key. Logs show `answered by keyN` and the token count, never the key.

`make eval-groq` runs `scripts/eval_groq.py`: the 40 golden questions and the edge cases in `data/eval/groq_edge_cases.yaml` through the real model, with the local-intent shortcut off, checking status, period window, chart type and totals against independent SQL. Add `--suite golden|edge`, `--ids E01,E24` or `--local-intent`. Results are written to `/tmp/groq_results.json` in the container. Rate-limit waits are retried, so a full run takes several minutes.
