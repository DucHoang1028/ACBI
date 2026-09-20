"""Authenticate, interpret, authorize, route, validate, execute and present."""

import logging
import re
import unicodedata
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.ai.budget import RequestBudget
from app.ai.client import FakeLLM, Intent
from app.api.auth import current_user
from app.chat.service import audit, get_context, save_context
from app.core.dates import intent_hints, is_confirmation
from app.history.service import save as save_result
from app.presentation.charts import TABLE, describe, validate_viz
from app.presentation.summary import factual, numbers_match
from app.query.builder import authorize, build, prepare, supports
from app.query.validation import (
    SQLCorrectionError,
    SQLPolicyError,
    ValidatedQuery,
    validate,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger("acbi.chat")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    language: Literal["vi", "en"] = "en"


def merged_intent(
    intent: Intent, prior: dict[str, Any] | None, question: str = ""
) -> Intent:
    slots = dict((prior or {}).get("slots") or {})
    current = intent.model_dump()
    hints = intent_hints(question)
    if not hints and is_confirmation(question):
        for turn in reversed((prior or {}).get("turns") or []):
            hints = intent_hints(turn.get("question", ""))
            if hints:
                break
    current.update(hints)
    for field in ("metric_id", "period", "factory_id", "territory"):
        if current[field] is None:
            current[field] = slots.get(field)
    if current["dimension"] == "none" and slots.get("dimension") not in (None, "none"):
        current["dimension"] = slots["dimension"]
    if current["period"] == "explicit" and not hints and intent.period is None:
        for field in ("start_date", "end_date"):
            if current[field] is None:
                current[field] = slots.get(field)
    missing = current["missing_fields"]
    if missing and "request" not in missing:
        unresolved = [
            field
            for field in missing
            if not current.get(field)
            or (field == "period" and current[field] == "recently")
        ]
        current["missing_fields"] = unresolved
        current["needs_clarification"] = bool(unresolved)
        if not unresolved:
            current["clarification_question"] = None
    if (
        "metric_id" in hints
        and current.get("metric_id")
        and current.get("period")
        and (
            current["period"] != "explicit"
            or (current.get("start_date") and current.get("end_date"))
        )
    ):
        current.update(
            needs_clarification=False,
            clarification_question=None,
            missing_fields=[],
        )
    return Intent.model_validate(current)


def next_turns(
    prior: dict[str, Any] | None, question: str, answer_text: str | None
) -> list[dict[str, str]]:
    turns = list((prior or {}).get("turns") or [])
    turns.append({"question": question, "answer": answer_text or ""})
    return turns[-6:]


def normalized(question: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", question.lower())
        if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")


SPECIAL_ROLES = {
    "factories": {"manager", "production"},
    "coverage": {"manager", "sales"},
    "factory_revenue": {"manager", "sales", "production"},
}


def special_kind(question: str) -> str | None:
    value = normalized(question)
    asks_factory = "nha may" in value or "factory" in value
    if asks_factory and re.search(
        r"\b(?:bao nhieu|nhung|ten|danh sach|how many|which)\b", value
    ):
        return "factories"
    if re.search(r"\b(?:du lieu|data)\b", value) and re.search(
        r"\b(?:nam|year|20\d\d)\b", value
    ):
        return "coverage"
    if asks_factory and re.search(r"\b(?:doanh thu|doanh so|revenue|sales)\b", value):
        return "factory_revenue"
    return None


def special_response(
    kind: str,
    engine: Any,
    role: str,
    language: str,
    anchor: date,
    anchor_source: str,
    request_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    vi = language == "vi"
    if kind == "factory_revenue":
        message = (
            "Doanh thu không thể phân theo nhà máy trong AdventureWorks vì đơn bán "
            "hàng không liên kết với factory. Tôi có thể so sánh sản lượng hoặc tỷ lệ "
            "phế phẩm của Factory A, B và C."
            if vi
            else "Revenue cannot be split by factory because sales orders are not "
            "linked to factories. I can compare production output or defect rate "
            "for Factory A, B and C."
        )
        result = response("needs_clarification", message, request_id, conversation_id)
        result["llm_calls"] = 0
        return result
    sql = (
        "SELECT name AS factory FROM acbi_demo.factory "
        + ("WHERE factory_id = 1 " if role == "production" else "")
        + "ORDER BY factory_id"
        if kind == "factories"
        else "SELECT EXTRACT(YEAR FROM orderdate)::int AS year,COUNT(*) AS orders "
        "FROM sales.salesorderheader GROUP BY 1 ORDER BY 1"
    )
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(
            text("SELECT set_config('statement_timeout', '15000', true)")
        )
        rows = [dict(row) for row in connection.execute(text(sql)).mappings()]
    if kind == "factories":
        names = ", ".join(str(row["factory"]) for row in rows)
        answer = (
            f"Database có {len(rows)} nhà máy: {names}."
            if vi
            else f"The database has {len(rows)} factories: {names}."
        )
    else:
        years = ", ".join(str(row["year"]) for row in rows)
        answer = (
            f"Dữ liệu đơn bán hàng có các năm: {years}."
            if vi
            else f"Sales-order data is available for: {years}."
        )
    result = response("ok", "Results found", request_id, conversation_id)
    result.update(
        answer_text=answer,
        table=rows,
        viz_config=TABLE.model_dump(),
        chart_fallback=True,
        sources={
            "source": "Adventureworks",
            "sql": sql,
            "parameters": {},
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "metric_versions": {},
            "data_as_of": anchor.isoformat(),
            "anchor_source": anchor_source,
            "references": [],
        },
        llm_calls=0,
    )
    return result


def clarification_text(intent: Intent, language: str) -> str:
    vi = language == "vi"
    if not intent.metric_id:
        return (
            "Tôi chưa có định nghĩa đã phê duyệt cho chỉ số này. Bạn có thể hỏi "
            "doanh thu, tăng trưởng doanh thu, sản lượng hoặc tỷ lệ phế phẩm."
            if vi
            else "This metric has no approved definition yet. Ask about revenue, "
            "revenue growth, production output, or defect rate."
        )
    if not intent.period or intent.period == "recently":
        return (
            "Bạn muốn xem kỳ nào? Ví dụ: tháng này, quý trước, năm 2024 "
            "hoặc một khoảng ngày."
            if vi
            else "Which period should I use? For example: this month, "
            "last quarter, 2024, or a date range."
        )
    return intent.clarification_question or (
        "Cần thêm một chi tiết để trả lời câu hỏi này."
        if vi
        else "One more detail is needed to answer this question."
    )


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


def chart(
    question: str, rows: list[dict[str, Any]], state: Any, budget: RequestBudget
) -> tuple[dict[str, Any], bool]:
    if not state.settings.external_metadata_enabled and not isinstance(
        state.llm, FakeLLM
    ):
        return TABLE.model_dump(), True
    error = None
    for _ in range(state.settings.llm_max_regenerations + 1):
        try:
            proposal = state.llm.visualize(question, describe(rows), error, budget)
            return validate_viz(proposal, rows).model_dump(), False
        except (ValueError, ValidationError) as invalid:
            error = str(invalid)[:180]
        except (httpx.HTTPError, RuntimeError, KeyError, TypeError):
            break
    return TABLE.model_dump(), True


def answer(
    body: AskRequest,
    user: dict[str, Any],
    request: Request,
) -> tuple[int, dict[str, Any]]:
    request_id = str(uuid4())
    conversation_id = body.conversation_id or str(uuid4())
    storage = request.app.state.storage
    settings = request.app.state.settings
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
        client = request.app.state.llm
        if client is None:
            return 503, response(
                "technical_failure",
                "Groq API key is not configured",
                request_id,
                conversation_id,
            )
        anchor = date.fromisoformat(request.app.state.readiness["data_as_of"])
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
                request.app.state.warehouse,
                user["role"],
                body.language,
                anchor,
                request.app.state.readiness["anchor_source"],
                request_id,
                conversation_id,
            )
            save_context(
                storage,
                conversation_id,
                user["id"],
                (prior or {}).get("slots") or {},
                result["message"] if kind == "factory_revenue" else None,
                next_turns(prior, body.question, result["message"]),
            )
            outcome = result["status"]
            return 200, result
        context = {
            "data_as_of": anchor.isoformat(),
            "slots": prior["slots"] if prior else {},
            "pending_question": prior.get("pending_question") if prior else None,
            "turns": (prior or {}).get("turns") or [],
        }
        raw = client.interpret(body.question, context, budget)
        intent = merged_intent(raw, prior, body.question)
        metric_id = intent.metric_id
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
                    intent.model_dump(),
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
                intent.model_dump(),
                question,
                next_turns(prior, body.question, question),
            )
            return 200, response(outcome, question, request_id, conversation_id)
        try:
            check_definition(intent, request.app.state.dictionary)
            plan, query_path, references = route_query(
                body.question, intent, user["role"], anchor, request.app.state, budget
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
                intent.model_dump(),
                question,
                next_turns(prior, body.question, question),
            )
            return 200, response(outcome, question, request_id, conversation_id)
        rows = run_query(request.app.state.warehouse, plan, budget)
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
                factual(plan.metric_id, rows, plan.start, plan.end, body.language)
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
                "anchor_source": request.app.state.readiness["anchor_source"],
                "references": references,
            },
        )
        if rows:
            result["viz_config"], result["chart_fallback"] = chart(
                body.question, rows, request.app.state, budget
            )
            if settings.send_results_to_llm and settings.external_results_enabled:
                try:
                    proposed = client.summarize(body.question, rows, budget)
                    if numbers_match(proposed, rows):
                        result["answer_text"] = proposed
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
                    logger.warning("Request %s summary fell back", request_id)
        result["llm_calls"] = budget.calls
        try:
            result["result_id"] = save_result(
                storage,
                user["id"],
                body.question,
                plan.metric_id,
                authorize(intent, user["role"]),
                result,
            )
            result["saved"] = True
        except SQLAlchemyError:
            logger.warning("Request %s result storage failed", request_id)
            result["status"] = "partial"
            result["message"] = "Answer ready, but saving failed"
            result["saved"] = False
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


def response(
    status: str, message: str, request_id: str, conversation_id: str
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "answer_text": None,
        "table": [],
        "viz_config": None,
        "sources": None,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "saved": False,
    }


@router.post("/chat/ask")
def ask(
    body: AskRequest, request: Request, user: dict[str, Any] = Depends(current_user)
) -> Any:
    from fastapi.responses import JSONResponse

    status_code, payload = answer(body, user, request)
    return JSONResponse(status_code=status_code, content=payload)
