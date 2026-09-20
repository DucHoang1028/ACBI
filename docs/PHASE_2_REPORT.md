# Phase 2 verification — 20 September 2026

Status: Phase 2 implemented and locally verified. Awaiting project-owner review before Phase 3.

The authenticated question endpoint accepts Vietnamese and English questions through a Groq structured-intent adapter. The model returns metric, dimension, period and filter slots only. A trusted builder selects static, parameterized SELECT templates for the four approved metrics. Role and Factory A restrictions are checked before warehouse access; unauthorized requests are denied without query execution. The external warehouse remains read-only and outside ACBI Compose.

The web app now has a question form, follow-up conversation, clarification and no-data states, a result table, and source details containing SQL, parameters, metric version and the 2025-06-29 data anchor. Conversation slots belong to one user in the application DB. Denied requests create a small audit record without the question text. Business results are not persisted yet.

The [Phase 2 verification report](phase2-verification.json) records 40/40 golden outcomes using FakeLLM, authenticated users and the live read-only AdventureWorks warehouse. The 32 query outcomes were reconciled with independent reference SQL; eight cases cover clarification and denials. Cross-user conversation reuse was rejected. Local unit tests (15), Ruff, Black, mypy and the production web build pass. Both ACBI application containers started healthy with the existing app database and external warehouse.

No `GROQ_API_KEY` was provided, so live Groq interpretation and its accuracy have **not** been tested. The UI states that live questions need the key. The adapter defaults to Groq `openai/gpt-oss-120b`, strict JSON-schema output, one call per request, a local 15-request/8,000-estimated-token per minute budget, and no automatic retries. The model never receives credentials, roles, SQL results or unrestricted schema details. A provider failure yields a technical-failure response.

The structured-intent path is implemented. RAG text-to-SQL, saved results, charts, voice, and full QS1–QS8 evaluation are outside Phase 2 and should not be presented as complete. Production exposure still needs HTTPS and secure cookie verification; the current site listens only on localhost.
