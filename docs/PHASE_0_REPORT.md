# Phase 0 status — 19 September 2026

Status: Phase 0 preparation and live checks complete; awaiting business-definition approval. All business definitions remain proposed, and Phase 1 has not started.

## Prepared

- FastAPI backend and Vietnamese/English React readiness page, with three ACBI Compose services and an internal-only application database.
- External AdventureWorks connection restricted to `acbi_ro`; administrative setup is separate from application code.
- Administrative factory extension, proposed dictionary with four metrics, four user specifications, and 40 golden questions: 32 SQL references and eight authorization/clarification contracts.
- Startup date-anchor validation and date resolution. Saved reference records, including no-SQL contracts, retain the anchor.
- Groq model candidate and budget configuration. No live LLM integration or provider calls in this phase.

## Verified warehouse data

These values were verified against the running warehouse on 19 September 2026.

| Item | Observed value |
| --- | --- |
| Database | `Adventureworks` |
| Sales header rows | 31,465 |
| Sales order dates | 2022-05-30 to 2025-06-29 |
| Default data anchor | 2025-06-29 |
| Work-order rows | 72,591 |
| Work-order end dates | 2022-06-12 to 2025-06-16 |
| Routing rows | 67,131 |
| Work orders without routing | 29,966 |
| Synthetic factories / location mappings | 3 / 14 |

The backend readiness endpoint returns the anchor above, four proposed metrics, zero approved metrics, and LLM calls disabled. The source warehouse folder was not edited or copied into ACBI.

## Validation

Source checks pass: 10 unit tests, Ruff lint, and strict mypy checks on 16 application source files. All three ACBI containers are healthy. The readiness page was checked in Vietnamese and English in a browser; it displays the 2025-06-29 anchor and the 4 proposed / 0 approved counts.

The live evaluation executed all 32 trusted reference SQL queries as `acbi_ro`. Three no-data cases returned no rows. Eight denial/clarification cases have no SQL and remain expected-behavior contracts, not passing runtime authorization tests. Territory totals, line/factory output totals, Factory A scope, product revenue allocation, and observed zero defect rates reconcile. Neither AI query path nor QS1–QS8 is claimed as implemented or evaluated.

The role check confirms `default_transaction_read_only=on`, `statement_timeout=15s`, no administrative attributes, no access to the humanresources schema, no work-order UPDATE privilege, and no CREATE privilege in public. Separate negative tests confirmed an UPDATE and restricted SELECT were denied and a 100 ms statement timeout was enforced. See [phase0-verification.json](phase0-verification.json) for counts and checks; complete local reference results are in gitignored `data/eval/results.local.json`.

Docker Desktop briefly failed on an inaccessible `dockerInference` runtime socket. It recovered without a factory reset. No warehouse disk or source data was deleted. The local Windows PostgreSQL port conflict still requires the documented Compose override.

## Approval decisions

Request approval of the proposed definitions before Phase 1. In particular:

1. Derive output as `orderqty - scrappedqty`, because this port has no `stockedqty` column.
2. Attribute each work order to its final routing operation; retain unrouted work orders as unassigned and manager-only. Factory mapping is synthetic; Factory C has no terminal work orders under this rule.
3. Allocate header revenue proportionally to detail net values for product/category breakdowns, preserving the header revenue definition.

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for all proposed definitions and deployment exceptions, including the local port-5432 conflict. Authentication, application permission enforcement, chat, history, and live Groq calls remain future work under the phased plan.
