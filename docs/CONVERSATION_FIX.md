# Conversation and UI corrections

The original intent prompt did not supply the warehouse date anchor. Its result
could request a month/year even when the question said “this month”. After a
date-only reply, the backend merged the previous metric but retained the model's
clarification flag, producing a repeated request for a metric already known.

The interpreter now receives DATA_AS_OF, previous slots and the pending
clarification. It identifies which fields actually need clarification. The
backend rechecks those fields after merging context; business ambiguity is still
preserved. Unambiguous Vietnamese/English relative calendar phrases resolve to
the existing period definitions, and explicit month/quarter replies use exclusive
end dates. A new partial date does not silently borrow an old ending date.

The frontend retains React and the three-container deployment. The new chat
layout has a history sidebar, visible user/assistant turns, a fixed composer,
question suggestions, date-anchor notice, compact tables and expandable sources.
Vietnamese responses format actual values and inclusive display dates. Revenue
remains in source currency; no VND/USD conversion or currency code was invented.
Active sessions renew every 12 minutes without remounting the chat.

Validation:

- 40 unit tests pass, including relative-quarter overrides, date-only follow-ups,
  preserved business ambiguity and Vietnamese factual summaries.
- 40 golden FakeLLM cases and conversation isolation pass against the warehouse.
- `scripts/verify_conversation.py` runs clarification, explicit month, last quarter
  and this month in one conversation, comparing every revenue value to independent
  SQL on AdventureWorks. It uses FakeLLM and no external AI calls.
- Live Groq tests returned `ok` for “Doanh thu tháng này là bao nhiêu?”,
  “Tháng 6 năm 2025”, and “Còn quý trước?” in the same conversation, with two
  model calls each. Last quarter resolved to 2025-01-01 through 2025-03-31.
- Frontend TypeScript/Vite production build, Ruff, Black and mypy pass.

This is targeted regression evidence, not proof of accuracy for arbitrary natural
language. Relative dates still intentionally use 2025-06-29. History reopening
currently displays one saved result; complete persisted transcript browsing is a
separate remaining limitation.
