"""Query Orchestration: interpret, authorize, route, execute, present, persist."""

import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.ai.budget import RequestBudget
from app.ai.client import FakeLLM, Intent
from app.conversation.intent import clarification_text, local_intent, merged_intent
from app.conversation.service import get_context, next_turns, save_context
from app.core.dates import is_share_question
from app.history.service import audit, latest_in_conversation
from app.history.service import save as save_result
from app.presentation.contract import AskRequest, response
from app.presentation.summary import factual, numbers_match, share_text
from app.presentation.visualization import (
    chart,
    requested_chart,
    reshape_kind,
    reshaped,
)
from app.query.builder import authorize, build, prepare, supports
from app.query.shortcuts import (
    SPECIAL_ROLES,
    special_domain,
    special_kind,
    special_response,
)
from app.query.validation import (
    SQLCorrectionError,
    SQLPolicyError,
    ValidatedQuery,
    validate,
)

logger = logging.getLogger("acbi.chat")


def run_query(
    engine: Any, plan: ValidatedQuery, budget: RequestBudget
) -> list[dict[str, Any]]:
    if not isinstance(plan, ValidatedQuery):
        raise SQLPolicyError("Query must pass the shared validator")
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": str(max(1, min(15000, int(budget.remaining() * 1000))))},
        )
        rows = [
            dict(r) for r in connection.execute(text(plan.sql), plan.params).mappings()
        ]
    if any(r.get("invalid_detail_count", 0) for r in rows):
        raise ValueError("Product revenue cannot be allocated: zero detail denominator")
    budget.remaining()
    for row in rows:
        row.pop("invalid_detail_count", None)
        for key, value in row.items():
            if isinstance(value, (Decimal, date, datetime)):
                row[key] = str(value)
    return rows


def check_definition(intent: Intent, dictionary: dict[str, Any]) -> None:
    metric = next(
        (m for m in dictionary["businessMetrics"] if m["metricId"] == intent.metric_id),
        None,
    )
    if not metric:
        raise ValueError("Choose an approved metric")
    approved = [d for d in metric["definitions"] if d["approvalStatus"] == "approved"]
    if not approved or max(d["version"] for d in approved) != 1:
        raise ValueError("This metric definition needs a verified query mapping")
    if intent.metric_id == "sales_growth" and intent.dimension not in {
        "none",
        "sales_territory",
    }:
        raise ValueError("Growth alignment for this dimension is not approved")
    dimension = (
        "date" if intent.dimension in {"day", "week", "month"} else intent.dimension
    )
    if dimension != "none" and dimension not in metric["supportedDimensions"]:
        raise ValueError("This dimension is not defined for the selected metric")
    if intent.series_dimension != "none":
        if intent.metric_id not in {"revenue", "production_output"}:
            raise ValueError(
                "Stacked breakdowns are only approved for revenue and production output"
            )
        series = (
            "date" if intent.series_dimension == "month" else intent.series_dimension
        )
        if series not in metric["supportedDimensions"]:
            raise ValueError("This dimension is not defined for the selected metric")
    if intent.factory_id not in (None, 1, 2, 3):
        raise ValueError("Choose Factory A, B or C")
    if intent.zero_scrap_only and (
        intent.metric_id != "defect_rate" or intent.dimension != "product"
    ):
        raise ValueError("Zero-scrap filtering requires defect rate by product")


def route_query(
    question: str,
    intent: Intent,
    role: str,
    anchor: date,
    state: Any,
    budget: RequestBudget,
) -> tuple[ValidatedQuery, str, list[str]]:
    if supports(intent):
        template = build(intent, role, anchor)
        return (
            validate(template, intent, role, trusted_template=True),
            "structured_intent",
            [],
        )
    if not state.settings.external_metadata_enabled and not isinstance(
        state.llm, FakeLLM
    ):
        raise ValueError("External metadata use needs approval")
    base = prepare(intent, role, anchor)
    references = state.retriever.retrieve(question, role, base.metric_id)
    if not references:
        raise ValueError("Approved metadata is insufficient for this request")
    error = None
    for attempt in range(state.settings.llm_max_regenerations + 1):
        candidate = state.llm.sql_candidate(question, intent, references, error, budget)
        if candidate.missing_information or not candidate.sql:
            raise ValueError(
                candidate.missing_information or "More information is required"
            )
        try:
            plan = validate(
                replace(base, sql=candidate.sql), intent, role, generated=True
            )
            return plan, "rag_text_to_sql", [r["id"] for r in references]
        except SQLCorrectionError:
            if attempt >= state.settings.llm_max_regenerations:
                raise RuntimeError("SQL correction limit reached")
            error = "Invalid PostgreSQL syntax. Return one SELECT statement."
    raise RuntimeError("No valid SQL candidate")


SLOT_FIELDS = (
    "metric_id",
    "dimension",
    "period",
    "start_date",
    "end_date",
    "factory_id",
    "territory",
    "series_dimension",
)


def carry_slots(prior: dict[str, Any] | None, intent: Intent) -> dict[str, Any]:
    """Slots after an unresolved turn: earlier context plus what this turn added."""
    slots = dict((prior or {}).get("slots") or {})
    for name in SLOT_FIELDS:
        value = getattr(intent, name)
        if value in (None, "none") or (name == "factory_id" and value not in (1, 2, 3)):
            continue
        slots[name] = value
    return slots


def persist(
    storage: Any,
    user_id: int,
    question: str,
    metric_id: str,
    factory_id: int | None,
    result: dict[str, Any],
    domain: str | None = None,
) -> None:
    """Save the answer with its provenance; say so plainly when that fails."""
    try:
        result["result_id"] = save_result(
            storage, user_id, question, metric_id, factory_id, result, domain
        )
        result["saved"] = True
    except SQLAlchemyError:
        logger.warning("Request %s result storage failed", result["request_id"])
        result["status"] = "partial"
        result["message"] = "Answer ready, but saving failed"
        result["saved"] = False


def answer(
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
) -> tuple[int, dict[str, Any]]:
    request_id = str(uuid4())
    conversation_id = body.conversation_id or str(uuid4())
    storage = state.storage
    settings = state.settings
    budget = RequestBudget(
        settings.request_timeout_seconds, settings.llm_max_calls_per_request
    )
    metric_id: str | None = None
    outcome = "technical_failure"
    query_path: str | None = None
    try:
        if body.conversation_id:
            prior = get_context(storage, conversation_id, user["id"])
            if prior is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            prior = None
        if user["role"] == "it_admin":
            outcome = "denied"
            return 403, response(
                outcome,
                "Business data is outside your scope",
                request_id,
                conversation_id,
            )
        client = state.llm
        if client is None:
            return 503, response(
                "technical_failure",
                "Groq API key is not configured",
                request_id,
                conversation_id,
            )
        anchor = date.fromisoformat(state.readiness["data_as_of"])
        target = reshape_kind(body.question)
        if target:
            latest = latest_in_conversation(
                storage, user["id"], conversation_id, user["role"]
            )
            if latest is None or not latest["payload"].get("table"):
                message = (
                    "Chưa có kết quả nào để hiển thị lại. "
                    "Hãy hỏi một câu về số liệu trước."
                    if body.language == "vi"
                    else "There is no earlier result to redisplay. "
                    "Ask a data question first."
                )
                outcome = "needs_clarification"
                return 200, response(outcome, message, request_id, conversation_id)
            previous = latest["payload"]
            viz, note = reshaped(target, previous["table"], body.language)
            result = response("ok", "Results found", request_id, conversation_id)
            result.update(
                table=previous["table"],
                sources=previous["sources"],
                viz_config=viz,
                chart_fallback=viz["type"] == "table",
                answer_text=note,
                llm_calls=0,
            )
            persist(
                storage,
                user["id"],
                body.question,
                latest["metric_id"],
                latest["factory_id"],
                result,
                latest["domain"],
            )
            save_context(
                storage,
                conversation_id,
                user["id"],
                (prior or {}).get("slots") or {},
                None,
                next_turns(prior, body.question, None),
            )
            outcome = result["status"]
            return 200, result
        kind = special_kind(body.question)
        if kind:
            if user["role"] not in SPECIAL_ROLES[kind]:
                outcome = "denied"
                return 403, response(
                    outcome,
                    "Data is outside your access scope",
                    request_id,
                    conversation_id,
                )
            result = special_response(
                kind,
                state.warehouse,
                user["role"],
                body.language,
                anchor,
                state.readiness["anchor_source"],
                request_id,
                conversation_id,
            )
            pending = result["message"] if kind == "factory_revenue" else None
            save_context(
                storage,
                conversation_id,
                user["id"],
                (prior or {}).get("slots") or {},
                pending,
                next_turns(prior, body.question, pending),
            )
            if result["status"] == "ok":
                persist(
                    storage,
                    user["id"],
                    body.question,
                    f"list:{kind}",
                    1 if user["role"] == "production" else None,
                    result,
                    special_domain(kind, user["role"]),
                )
            outcome = result["status"]
            return 200, result
        context = {
            "data_as_of": anchor.isoformat(),
            "slots": prior["slots"] if prior else {},
            "pending_question": prior.get("pending_question") if prior else None,
            "turns": (prior or {}).get("turns") or [],
        }
        raw = (
            local_intent(body.question) if settings.local_intent_enabled else None
        ) or client.interpret(body.question, context, budget)
        intent = merged_intent(raw, prior, body.question)
        metric_id = intent.metric_id
        share_of: list[str] | None = None
        if intent.territory and is_share_question(body.question):
            # Query every territory, then compute the share from the result rows.
            share_of = intent.territory.split("|")
            intent = intent.model_copy(
                update={"territory": None, "dimension": "sales_territory"}
            )
        if metric_id:
            try:
                authorize(intent, user["role"])
            except PermissionError:
                outcome = "denied"
                return 403, response(
                    outcome,
                    "Data is outside your access scope",
                    request_id,
                    conversation_id,
                )
            except ValueError as error:
                outcome = "needs_clarification"
                question = str(error)
                save_context(
                    storage,
                    conversation_id,
                    user["id"],
                    carry_slots(prior, intent),
                    question,
                    next_turns(prior, body.question, question),
                )
                return 200, response(outcome, question, request_id, conversation_id)
        if (
            intent.needs_clarification
            or not intent.metric_id
            or not intent.period
            or intent.period == "recently"
        ):
            outcome = "needs_clarification"
            question = clarification_text(intent, body.language)
            save_context(
                storage,
                conversation_id,
                user["id"],
                carry_slots(prior, intent),
                question,
                next_turns(prior, body.question, question),
            )
            return 200, response(outcome, question, request_id, conversation_id)
        try:
            check_definition(intent, state.dictionary)
            plan, query_path, references = route_query(
                body.question, intent, user["role"], anchor, state, budget
            )
        except PermissionError:
            outcome = "denied"
            return 403, response(
                outcome,
                "Data is outside your access scope",
                request_id,
                conversation_id,
            )
        except ValueError as error:
            outcome = "needs_clarification"
            question = str(error)
            save_context(
                storage,
                conversation_id,
                user["id"],
                carry_slots(prior, intent),
                question,
                next_turns(prior, body.question, question),
            )
            return 200, response(outcome, question, request_id, conversation_id)
        rows = run_query(state.warehouse, plan, budget)
        if intent.series_dimension != "none" and len(rows) >= min(intent.limit, 250):
            outcome = "needs_clarification"
            question = (
                "Có quá nhiều nhóm để vẽ đầy đủ. Hãy thu hẹp khoảng thời gian "
                "hoặc chọn ít nhóm hơn."
                if body.language == "vi"
                else "There are too many groups to show in full. Narrow the period "
                "or choose fewer groups."
            )
            save_context(
                storage,
                conversation_id,
                user["id"],
                carry_slots(prior, intent),
                question,
                next_turns(prior, body.question, question),
            )
            return 200, response(outcome, question, request_id, conversation_id)
        outcome = "ok" if rows else "no_data"
        save_context(
            storage,
            conversation_id,
            user["id"],
            intent.model_dump(),
            None,
            next_turns(prior, body.question, None),
        )
        result = response(
            outcome,
            "Results found" if rows else "No data for this period",
            request_id,
            conversation_id,
        )
        result.update(
            table=rows,
            query_path=query_path,
            answer_text=(
                (
                    share_of
                    and plan.metric_id == "revenue"
                    and share_text(rows, share_of, plan.start, plan.end, body.language)
                )
                or factual(plan.metric_id, rows, plan.start, plan.end, body.language)
            ),
            sources={
                "source": "Adventureworks",
                "sql": plan.sql,
                "parameters": {
                    k: str(v) if isinstance(v, date) else v
                    for k, v in plan.params.items()
                },
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "metric_versions": {plan.metric_id: plan.version},
                "data_as_of": anchor.isoformat(),
                "anchor_source": state.readiness["anchor_source"],
                "references": references,
            },
        )
        if rows:
            wanted = (
                "stacked_bar"
                if intent.series_dimension != "none"
                else requested_chart(body.question)
            )
            if wanted:
                # The user named the chart type: map result columns deterministically.
                viz, note = reshaped(wanted, rows, body.language)
                result["viz_config"] = viz
                result["chart_fallback"] = viz["type"] == "table"
                if viz["type"] != wanted:
                    result["answer_text"] = f"{result['answer_text']} {note}"
            else:
                result["viz_config"], result["chart_fallback"] = chart(
                    body.question, rows, state, budget
                )
            if settings.send_results_to_llm and settings.external_results_enabled:
                try:
                    proposed = client.summarize(body.question, rows, budget)
                    if numbers_match(proposed, rows):
                        result["answer_text"] = proposed
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
                    logger.warning("Request %s summary fell back", request_id)
        result["llm_calls"] = budget.calls
        persist(
            storage,
            user["id"],
            body.question,
            plan.metric_id,
            authorize(intent, user["role"]),
            result,
        )
        return 200, result
    except HTTPException:
        raise
    except (
        SQLAlchemyError,
        httpx.HTTPError,
        ValidationError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        logger.warning("Request %s failed: %s", request_id, type(error).__name__)
        return 503, response(
            "technical_failure",
            "Question could not be processed",
            request_id,
            conversation_id,
        )
    finally:
        logger.info(
            "request_id=%s llm_calls=%d outcome=%s path=%s",
            request_id,
            budget.calls,
            outcome,
            query_path,
        )
        if outcome == "denied":
            audit(storage, user["id"], request_id, outcome, metric_id)
