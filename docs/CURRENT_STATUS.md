# Current system status — 20 September 2026

ACBI is a working, authenticated prototype with a public HTTPS demo. It is not an
always-on production service. The current temporary link is in README.

## Core components

| Component | Current responsibility |
| --- | --- |
| React/TypeScript frontend | Vietnamese/English login, questions, editable voice transcripts, tables, charts, saved results, and administration. |
| Nginx web container | Serves the frontend and proxies API requests; demo login throttling and forwarded HTTPS scheme. |
| FastAPI backend | Authentication, fixed role scopes, question orchestration, clarification/context, SQL validation, chart validation, history and audit. |
| Query processing | Approved structured SQL templates plus scoped BM25 retrieval and Groq SQL proposals; validation before read-only execution. |
| Groq | `openai/gpt-oss-120b` for structured interpretation and proposals; `whisper-large-v3-turbo` for transcription. |
| Application PostgreSQL | Accounts, hashed passwords, sessions, conversation context, saved rows/charts/provenance, and audit records; internal-only Docker network. |
| External AdventureWorks | Source data accessed as `acbi_ro` over verified TLS; the synthetic factory extension remains explicitly documented. |
| Cloudflare Quick Tunnel | Public HTTPS access to the PC's loopback web port; separate Windows process, outside the three ACBI containers. |

## Accounts

All four existing accounts passed public HTTPS login, current-role metadata,
Secure refresh-cookie, refresh, and logout checks after the tunnel restart.

| Username | Role and scope |
| --- | --- |
| `manager` | All four approved metrics, all factories; only its own saved results. |
| `sales` | Revenue and sales growth; no production or quality access. |
| `production_a` | Production output and defect rate, Factory A only. |
| `it_admin` | User administration and system/audit views; no business-metric access. |

Passwords are available only in private `deploy/seed-credentials.txt`, excluded
from GitHub. These accounts are not anonymous public-demo credentials.

## Current limitations

1. **Availability:** the PC, Docker, warehouse and tunnel must remain running.
   Restarting the tunnel changes the URL, so README must be updated. There is no
   failover or availability guarantee. Automated backup and recovery have not
   been configured or verified.
2. **Business scope:** only revenue, sales growth, production output and defect
   rate have approved implementations. This is sample data, not a live company
   feed. Relative dates use 2025-06-29, not today's date. Factories are synthetic;
   production-line attribution uses the terminal routing step. The PostgreSQL
   port lacks `stockedqty`, so approved output is `orderqty - scrappedqty`.
3. **AI accuracy:** passing deterministic reference tests does not establish
   live-model accuracy. Only a small number of real Groq questions were smoke
   tested. Complex or unsupported questions can require clarification or fail.
   SQL validation does not prove every proposed business interpretation correct.
4. **Capacity:** current application limits are 15 LLM calls/minute, 8,000
   estimated tokens/minute, and at most three calls per question, with a
   30-second request budget. Provider quotas may be stricter. Three-call questions
   allow at most roughly five questions/minute before other constraints. These
   budgets are shared by this backend instance, not allocated per user. The
   five-concurrent-request performance check used FakeLLM, not real Groq.
5. **Sessions:** access tokens expire after 15 minutes. The frontend now refreshes
   on page load and every 12 minutes while active, preserving the current chat.
   Browser sleep or a network outage can still interrupt renewal; reload or sign
   in again if needed. There is no automatic replay of a failed question.
6. **History:** reopening a saved conversation restores every saved answer in order,
   plus clarification turns among the last six remembered. The last open conversation
   is restored after a page reload. Denied and failed questions are not stored, so they
   do not reappear. History holds at most 200 saved results per conversation and has no
   search or export workflow.
7. **Voice:** browser recording stops after 60 seconds and the backend accepts
   at most 10 MB. Real Vietnamese/English transcription quality has not been
   evaluated with user audio. Transcription currently uses a synchronous network
   call inside an async endpoint, so a slow voice request can delay other work.
8. **Access management:** the four role policies and Factory A restriction are
   fixed. There is no custom permission editor, SSO, MFA, or self-service password
   reset. Warehouse role `acbi_ro` enforces read-only access; per-user scope is
   enforced by ACBI rather than separate PostgreSQL roles or row-level security.
9. **Operations:** HTTPS and verified warehouse TLS work, but independent security
   review, production load testing, and operational alerting remain outstanding.
   The warehouse certificate needs renewal before its one-year expiry. GitHub
   Actions was blocked by the owner's account billing lock; local checks are
   available. The shared login throttle can affect several demo viewers at once.
10. **External data processing:** approved metadata and questions go to Groq;
    submitted voice audio goes to Groq transcription. Raw query-result export to
    the LLM remains disabled. Factual summaries are generated from validated rows
    locally, and visualizations are checked before rendering.

The practical next improvements are complete history browsing,
nonblocking voice processing, broader live-model evaluation, and a
stable host with tested backups.
