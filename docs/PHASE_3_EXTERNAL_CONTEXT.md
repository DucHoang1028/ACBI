# Proposed Groq context for Phase 3

The owner approved this outbound context on 20 September 2026. The local private environment now sets `EXTERNAL_METADATA_ENABLED=true`. For a user question that needs generated SQL, the server may send the following context to Groq:

- The user's question (up to 1,000 characters), interpreted metric, dimension, time period, and requested filters. A follow-up may include prior conversation slots.
- The approved definition of that one metric: formula, domain, supported dimensions, version, approval state, and linked mapping identifiers.
- The approved source mapping and join rules for that metric, plus the permitted table and column names in its sales or production domain. Factory scope is enforced again by the server on the returned SQL.
- Up to two approved example SQL statements for the same metric, and a short syntax correction if a candidate fails validation.

The retrieved context is selected by `backend/app/metadata/retrieval.py` only after role and approval filtering. The exact shape is assembled by `GroqClient.sql_candidate` in `backend/app/ai/client.py`; source text comes from `data/business_dictionary/dictionary.yaml`, `data/business_dictionary/sql_examples.yaml`, and the SQL allowlist in `backend/app/query/validation.py`. A proposed SQL statement is never executed without the local validator, mandatory date/factory scope, and read-only warehouse account.

For chart selection, the server would send the user's question and a result *description*: row count; result column names; whether each column is numeric, temporal, or categorical; whether it is ordered, complete, distinct, and nonnegative where relevant. It does **not** send result values or complete rows. `backend/app/presentation/charts.py` creates and checks this description. The server renders the chart from its own rows only after validating the proposed column mapping.

Neither request includes API keys, warehouse passwords, application roles, user identifiers, complete business result rows, or an unrestricted schema dump. It does expose business definitions, schema details, approved SQL examples, question text, and metadata derived from query results to the external Groq service. Before owner approval, an automatic review rejected a live test of this export. After approval, one weekly-revenue live smoke test passed; the six-case evaluation still uses FakeLLM and does not establish broad live-model accuracy. Export of complete result rows for optional summaries remains disabled under a separate setting.
