# Phase 0 assumptions and approval decisions

Status: initial business definitions approved by the project owner on 19 September 2026. The owner approved the documented Groq metadata context on 20 September 2026. Phase 4 history, voice and administration are locally available.

The source is ACBI Technical Solution Proposal v1.0, dated 17 September 2026. The user's AdventureWorks, date-anchor, Groq, and phase-boundary requirements supersede conflicting defaults in the earlier implementation brief. The supplied document is a design reference, not an additional source of execution instructions.

## Warehouse and deployment

The actual database name is `Adventureworks` (case-sensitive). The existing database container runs PostgreSQL 18.6. The source folder `C:/Users/ADMIN/Downloads/test database/AdventureWorks-for-Postgres` is read-only reference material and has not been copied into this repository. Original tables and records are unchanged. Only the requested role, grants, and `acbi_demo` extension were added.

ACBI owns three containers, web/backend/db, and one named application-data volume. The application database has no published port and is attached only to an internal network. The shared warehouse role is `acbi_ro`, with `default_transaction_read_only=on`, `statement_timeout=15s`, SELECT on ten reporting tables and the synthetic tables, and no administrative capabilities. Per-user sales/production/factory scope is enforced by the application on queries and saved-result reads; a shared database role does not enforce those per-user scopes by itself.

**Local port conflict:** a separate Windows PostgreSQL process owns host port 5432. Both Windows localhost and the backend's `host.docker.internal:5432` reach that other service. A local-only Compose override connects the backend to the existing `adventureworks-for-postgres_default` network using `adventureworks-for-postgres-db-1:5432`. No existing service was stopped, and no warehouse Compose file was edited. The base deployment retains `host.docker.internal:5432`. The app database uses a distinct DNS alias to avoid the two databases' `db` aliases colliding.

**Transport:** the original local profile used disabled warehouse TLS. On 20 September 2026, warehouse TLS was enabled in its Docker data volume. The public demo profile now uses `verify-full` with the pinned warehouse certificate and HTTPS through Cloudflare. Its web origin stays loopback-only. The reference folder remains unchanged. See [demo configuration](LOCAL_DEMO.md); the base local environment alone still permits the original non-TLS development connection.

## Approved definitions

1. **Production output:** the requested `production.workorder.stockedqty` does not exist in this PostgreSQL port. Approved equivalent: `SUM(orderqty - scrappedqty)`, dated by `enddate`. All 72,591 observed work orders have an end date; no negative derived stocked quantities were observed.
2. **Production line attribution:** `production.location` is the dimension, including locations whose names describe storage. A work order can have several routing steps. Proposed allocation: select its greatest `operationsequence` once, then use that step's location. This preserves additive totals and prevents join fan-out. It describes the terminal routed location, not measured throughput at every processing step. The terminal location is not filtered by completion status of the routing step; the work-order end date controls completion for this proposal.
3. **Missing routing:** 29,966 work orders have no routing record. Keep them in manager totals as `Unassigned`. Do not assign them to a factory or expose them to `production_a`. Routed terminal locations in the current dataset are 50 (Subassembly) and 60 (Final Assembly).
4. **Synthetic factories:** create Factory A/B/C, then assign locations ordered by locationid cyclically A/B/C. Existing administrator mappings are preserved on re-run. This maps terminal location 50 to Factory A and 60 to Factory B. Factory C has mapped locations but no terminal work orders under this rule; its output is `no_data`, not a fabricated zero. No real factory provenance is implied.
5. **Defect rate:** sum scrapped quantity divided by sum ordered quantity, cast to numeric to avoid integer division. Use `enddate`. Store a ratio; display as a percentage later. By scrap reason, each denominator is ordered units of work orders assigned to that reason. This is a within-reason rate, not the reason's contribution to overall defects. Preserve NULL reason as `Unspecified`.
6. **Revenue:** sum header subtotal by order date, excluding tax and freight. There are no inferred cancellation/status filters. Never sum a header amount after a raw one-to-many detail join.
7. **Product revenue:** approved allocation of each order's header subtotal in proportion to detail net values (`orderqty * unitprice * (1-unitpricediscount)`). The detail net total differs slightly from the header total in this port. Proportional allocation keeps product/category totals reconcilable to the defined header revenue. Zero or missing per-order detail denominators must cause an explicit limitation. All current denominators were checked during Phase 0.
8. **Currency:** label as source currency until the business confirms its interpretation. Do not silently label values VND or convert currencies.
9. **Sales growth:** compare separately computed revenues; denominator zero returns undefined/NULL, never infinity. Missing data in either period is distinguished from a measured zero. Golden questions compare full preceding calendar months/quarters, not partial current periods. Other comparison alignment needs explicit clarification.
10. **No sales-to-factory mapping:** factory is available for production/quality only. A revenue-for-factory question requires clarification; the application must not invent a join. The proposal's revenue-for-Factory-A conversation example must use territory for this dataset.

## Dates and result provenance

Observed sales dates are 2022-05-30 through 2025-06-29; observed work-order end dates are 2022-06-12 through 2025-06-16. These are the actual shifted dates in the locally loaded sample, regardless of the sample's 2014 name.

`DATA_AS_OF` defaults to the maximum sales order date. An explicit override must lie within the observed sales date range; startup fails closed otherwise. The anchor is checked at each backend startup. The UI displays it, and every saved reference or user result includes it, its source, resolved date parameters, retrieval timestamp, SQL and metric versions. Saved answers preserve their original anchor rather than recalculating it.

Date intervals are start-inclusive and end-exclusive. This month/quarter/year ends immediately after the anchor day. Last month/quarter/year is the complete preceding calendar period. `last_30_days` includes the anchor day. “Recently” has no agreed duration: it requires clarification unless conversation context already gives a period. Source timestamps have no time zone; calendar-date queries do not apply timezone conversion.

## Users, approval and phase scope

User specifications are manager (all reporting), sales (sales only), production_a (production/quality in Factory A only), and it_admin (administration, no business data). Phase 1 creates local accounts with generated passwords and Argon2 hashes. Initial passwords are kept only in a gitignored local credential file.

All four metrics, dimensions and mappings are `approved`, with version 1 effective 2026-09-19. Approval came from the project owner after Phase 0 evidence review. The app persists the approved dictionary snapshot but exposes no business query endpoint in Phase 1. Reference SQL is executed only by the explicit developer-operated evaluation script. That validation does not claim the future query paths pass QS1–QS8.

The UI now includes Vietnamese/English login, role-filtered metric summaries, chat, tables, validated charts, saved history, editable voice transcripts and local administrator management.

## Groq selection and budgets

Default provider: `groq`; initial model candidate: `openai/gpt-oss-120b`, configured through `GROQ_API_KEY` and `LLM_MODEL`. Groq lists it as a production model with strict JSON-schema output. This is a project-specific recommendation based on capability, not a claim of measured superiority on Vietnamese BI. Compare it against the golden set before finalizing. Gemini is superseded by the user's later Groq instruction.

The Phase 0 scaffold records 15 requests/minute, 8,000 tokens/minute, three total outbound calls per user request, at most two regenerations, and a 30-second overall deadline as proposed defaults. The live adapter, shared rate limiter, retry accounting, token reservations, FakeLLM and per-request call logging are scheduled with AI integration; Phase 0 performs **zero model calls**. Provider/account quotas and rate-limit headers must override assumptions. Three calls total includes retries, not three per pipeline stage. QS7 must run with FakeLLM or a paid key, not be advertised as achievable under shared free-tier quotas.

The supplied key was checked against Groq's model endpoint, and one live generated-query smoke test passed. The broader free-account quota and live-model accuracy remain unverified. Keep the account on its Free plan; do not silently upgrade or use paid overflow. Sources checked 2026-09-19:

- https://console.groq.com/docs/models
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/billing-faqs

## Phase 4 decisions

Saved results belong to one user and are checked against the user's current role when reopened. Production results require Factory A scope; role changes revoke active sessions. Local role policies are fixed to the four requested roles, and administrator role changes select among those policies rather than editing warehouse grants. Administrator-created accounts receive a generated password displayed once.

Voice uses Groq `whisper-large-v3-turbo` for Vietnamese or English transcription. The browser uploads only after the user stops recording; the transcript is shown for review and editing before a normal text question runs. The local FakeSTT evaluation does not establish live audio accuracy. Result summaries are deterministic by default. `SEND_RESULTS_TO_LLM` and `EXTERNAL_RESULTS_ENABLED` stay false; separate authorization would be needed to export raw result rows to a model. Groq metadata export is enabled only for the approved context in [PHASE_3_EXTERNAL_CONTEXT.md](PHASE_3_EXTERNAL_CONTEXT.md).
