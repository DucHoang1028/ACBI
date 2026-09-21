# ACBI architecture and phase boundary

One FastAPI backend contains the API, core, auth, conversation, AI, metadata, query, presentation, history and admin modules. React/TypeScript/Vite is served by Nginx. PostgreSQL stores application data on an internal network. AdventureWorks and Groq are external dependencies. The AI proposes; the backend validates and owns execution.

The proposal's eight backend responsibilities are retained: Query Orchestration, Authentication and Authorization, Conversation Management, AI Integration, Business Metadata and Retrieval, Query Processing, Result Presentation, History and Audit. Where each responsibility lives in `backend/app`:

| Proposal module | Code |
| --- | --- |
| Query Orchestration | `query/orchestrator.py` (the eleven-step pipeline, `answer`); `api/chat.py` is only the HTTP wrapper |
| Authentication and Authorization | `auth/`, `api/auth.py`; `query/builder.authorize` for metric, factory and territory scope |
| Conversation Management | `conversation/service.py` (slots, turns, pending clarification), `conversation/intent.py` (merge context, validate factory, territory and breakdown choices), `conversation/dialogue.py` (grounded answers about the data and small talk) |
| AI Integration | `ai/client.py` (`LLMClient`, `GroqClient`, `FakeLLM`), `ai/keys.py` (key pool and failover), `ai/budget.py` (`RequestBudget`), `ai/stt.py` |
| Business Metadata and Retrieval | `metadata/` (dictionary, `vocabulary.py` names, synonyms and members from the dictionary and the data, scoped BM25 retriever) |
| Query Processing | `query/builder.py` (approved templates), `query/validation.py` (shared sqlglot gate), `query/forecast.py` (labelled trend forecast, refusable) |
| Result Presentation | `presentation/charts.py` (config validation), `presentation/visualization.py` (AI proposal loop, named chart types), `presentation/summary.py`, `presentation/analysis.py` (comparisons and highlights computed from rows), `presentation/messages.py` (no raw internal errors), `presentation/contract.py` (response contract) |
| History and Audit | `history/service.py` (saved results, transcript, `access_audit`), `api/history.py` |

No separate service or autonomous agent is introduced.

Phase 0 established warehouse connectivity, historical date resolution, metadata preparation and trusted reference evaluation. Phase 1 added authenticated local accounts and role-filtered metadata. Phase 2 added structured intent, static allowlisted SQL templates, bound parameters, date-aware reporting and per-user factory scope enforcement. Phase 3 added scoped BM25 retrieval, generated SQL proposals for approved question shapes, shared SQL validation, and checked visualization mappings. Phase 4 adds traceable saved answers, current-role checks on history, editable speech transcription, local user management, factual summaries, and controlled storage-failure responses. There is no public SQL execution endpoint. The model's SQL remains a proposal until the backend validates it. Live metadata export is enabled after owner approval; result-row export remains disabled.

The Business Dictionary follows Figure 8.1: BusinessMetric has versioned MetricDefinitions; each definition references DataMappings; each mapping references a DataSource; metrics declare supported Dimensions. Only approved definitions may be selected for business execution. The initial four definitions are approved at version 1, with assumptions recorded in `ASSUMPTIONS.md`.

## Error responses and remaining work

This preserves the eight situations from proposal Section 8.6.

| Situation | Required response |
| --- | --- |
| Missing or ambiguous information | Clarify before executing a query. |
| Unauthorized request | Deny and audit; no restricted query. |
| No matching records | Explain that no data matches, distinct from zero. |
| Invalid AI output or correctable SQL error | Bounded correction; validate every revision. |
| AI timeout or temporary unavailability | Bounded retries within deadline, then controlled failure. |
| Database failure or timeout | Technical failure, never no_data. |
| Invalid visualization or failed optional summary | Return validated table/factual response. |
| Result storage failure | Report not saved; never claim successful persistence. |

Phase 2 handles clarification, denial, no-data and technical-failure outcomes in the structured-intent path. Phase 3 added BM25 retrieval, RAG Text-to-SQL and visualization proposals, locally verified with FakeLLM and smoke-checked once with Groq. Its approved external context is described in [PHASE_3_EXTERNAL_CONTEXT.md](PHASE_3_EXTERNAL_CONTEXT.md). Phase 4 adds the remaining user flows and a local [quality report](PHASE_4_REPORT.md). Broader live-model accuracy and production deployment remain unverified.
