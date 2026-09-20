# Phase 1 verification — 20 September 2026

Status: Phase 1 implemented and locally verified. Awaiting project-owner review before Phase 2.

## Delivered

- Four Phase 0 metric definitions, seven dimensions, and two fact mappings recorded as approved, version 1, effective 2026-09-19. `stockedqty` remains an explicit derived quantity; terminal routing and product revenue allocation retain the reviewed assumptions.
- Three running ACBI containers. The app DB is internal-only; AdventureWorks stays external. The backend checks the warehouse date range at startup and connects only as `acbi_ro`.
- An application-only account and session schema, four seeded accounts, Argon2 password hashes, 15-minute bearer access tokens, seven-day rotating refresh cookies, five-attempt login lockout, and logout revocation. Initial passwords reside in gitignored `deploy/seed-credentials.txt`, not source code or command output.
- Local credentials and full reference result rows are excluded from Docker build context and final application images. The repeatable role test reads passwords from the private local file through standard input; its report contains no secrets.
- Vietnamese/English local sign-in page showing the 2025-06-29 anchor, zero proposed and four approved metrics. Authenticated users see only their role's approved metric summaries. Manager sees four, sales two, production_a two with Factory A scope, and it_admin zero. Only it_admin can access the status endpoint.

## Checks

The [Phase 1 security report](phase1-verification.json) records successful login for all four roles, metadata scoping, admin access denial, refresh rotation, old-token replay rejection, logout revocation, anonymous denial, incorrect-password denial, and cross-origin denial. A browser-facing proxy check returned HTTP 200 for sales login and logout and two sales metrics. No credentials or tokens appear in either report.

The [warehouse verification](phase0-verification.json) was rerun with the approved dictionary: 32 reference SQL queries passed as `acbi_ro`; eight no-SQL question cases remain contracts only. Sales dates are 2022-05-30 through 2025-06-29, work-order end dates 2022-06-12 through 2025-06-16, and all reference reconciliations passed. The role has read-only transactions, a 15-second timeout, no administrative privileges, and no access to humanresources or UPDATE. The browser displayed the anchor and 0/4 counts correctly. Twelve unit tests, Ruff, Black and strict mypy pass.

## Boundary and deployment note

No business query, conversation, saved-result, voice, or live Groq endpoint is available yet. Role-filtered metric metadata does not itself prove authorization of SQL queries; Phase 2 must apply factory and domain filters before query execution and test denied requests on both future query paths. Reference SQL evaluation is an explicit developer-only check, not a passing QS1–QS8 application evaluation.

The current web port is bound to localhost over HTTP. Refresh cookies are HttpOnly and SameSite=Strict, with HTTPS security based on request scheme. Before exposure outside localhost, configure trusted HTTPS termination and verify Secure cookie behavior. The local Windows port-5432 conflict still requires `deploy/docker-compose.local.yml`.
