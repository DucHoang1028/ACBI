# Phase 4 API

All local browser endpoints use `/api/` through Nginx. The backend publishes no host port. OpenAPI is available at `http://localhost:8080/openapi.json`.

- `GET /health`, `GET /api/bootstrap`: process readiness, source date anchor, approved/proposed metric counts, live LLM state, and advanced-analysis gate. No business rows.
- `POST /api/auth/login`: JSON `{username,password}`. Returns a 15-minute opaque bearer access token and a seven-day HttpOnly, SameSite=Strict refresh cookie. Failed login uses a generic 401; five failures lock the account for five minutes.
- `POST /api/auth/refresh`: rotates both tokens; old access and refresh tokens stop working.
- `POST /api/auth/logout`: revokes the current refresh session and clears its cookie.
- `GET /api/auth/me`: bearer token required; returns username, role, domains and factory scope.
- `GET /api/metadata`: bearer token required; returns only approved metric summaries within the caller's role and the caller's factory scope. No raw warehouse metadata or business rows.
- `GET /api/admin/status`: bearer token required; `it_admin` only. Returns local status and anchor.
- `POST /api/chat/ask`: bearer token required; JSON `{question,conversation_id?}`. Returns `status`, `answer_text`, `table`, validated `viz_config`, query path, request and conversation IDs, saved-result ID, and source details including SQL, bound parameters, definition version and date anchor. A denied request does not query the warehouse. An answer that cannot be saved returns `partial` with `saved=false`.
- `POST /api/chat/voice`: bearer token required; multipart `file` and optional `language` (`vi` or `en`). Accepts up to 10 MB of WebM, Ogg, WAV, MP3, MP4 or M4A audio from business roles. Returns an editable transcript only; it does not execute a question. Unsupported/empty audio and provider failure return controlled errors.
- `GET /api/conversations`: returns the current user's visible saved conversations. `GET /api/conversations/{id}` lists visible saved results in one conversation. `GET /api/results/{id}` reopens the stored answer and original provenance. Ownership and current role/factory scope are checked again on every read.
- `GET /api/admin/users`, `POST /api/admin/users`, `PATCH /api/admin/users/{id}`: `it_admin` only. List accounts, create one with a generated password returned once, or change role/enabled state. Role changes revoke sessions.
- `GET /api/admin/roles`, `GET /api/admin/scopes`, `GET /api/admin/audit`: `it_admin` only. Show fixed role policies, effective factory scope, and recent denial audit without question text.

Manager can see all four approved metrics. Sales sees only revenue and sales growth. Production A sees only production output and defect rate, with Factory 1 as required scope. IT admin sees no business metrics. The application role is independent of the shared warehouse `acbi_ro` connection. The backend checks each role before execution and injects warehouse date and factory scope into generated queries. The local advanced-analysis gate is enabled after owner approval; result rows are still never sent to the model by default.

Login and refresh cookie requests enforce same-origin when an Origin header is supplied. Access tokens stay in browser memory. Local HTTP is loopback-only; production requires HTTPS and a secure-cookie deployment configuration before exposure outside localhost.
