"""Query Orchestration: interpret, authorize, route, execute, present, persist."""

import calendar
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from app.conversation.dialogue import fallback, reply_from_metadata
from app.conversation.intent import (
    clarification_text,
    local_intent,
    merged_intent,
    validate_choices,
)
from app.conversation.service import get_context, next_turns, save_context
from app.core.dates import is_share_question, metric_words, month_start
from app.core.text import fold
from app.history.service import audit, latest_in_conversation
from app.history.service import save as save_result
from app.metadata import vocabulary
from app.presentation.analysis import analysis_kind, analyze
from app.presentation.charts import TABLE, VizConfig, validate_viz
from app.presentation.contract import AskRequest, response
from app.presentation.messages import Explained, friendly
from app.presentation.summary import factual, numbers_match, share_text
from app.presentation.visualization import (
    chart,
    requested_chart,
    reshape_kind,
    reshaped,
)
from app.query.builder import authorize, build, prepare, supports
from app.query.forecast import (
    METHOD_VERSION,
    ForecastRefused,
    contiguous,
    make_forecast,
    next_months,
)
from app.query.validation import (
    SQLCorrectionError,
    SQLPolicyError,
    SQLScopeError,
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
    known = set(vocabulary.get().ids("factory").values())
    if intent.factory_id is not None and known and intent.factory_id not in known:
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
            raise Explained(
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


def run_dialogue(
    raw: Intent,
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
    budget: RequestBudget,
    anchor: date,
    request_id: str,
    conversation_id: str,
    prior: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Answer about the data itself, or decline what the system cannot serve."""
    limitation = raw.intent_type == "unsupported"
    mode = "limitation" if limitation else "answer"
    text_out = reply_from_metadata(
        state.llm, body.question, user["role"], mode, anchor.isoformat(), budget
    ) or fallback(mode, body.language, user["role"])
    status = "needs_clarification" if limitation else "ok"
    save_context(
        state.storage,
        conversation_id,
        user["id"],
        (prior or {}).get("slots") or {},
        None,
        next_turns(prior, body.question, text_out),
    )
    result = response(
        status, text_out if limitation else "Results found", request_id, conversation_id
    )
    result.update(answer_text=None if limitation else text_out, llm_calls=budget.calls)
    return status, result


def run_forecast(
    raw: Intent,
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
    budget: RequestBudget,
    anchor: date,
    request_id: str,
    conversation_id: str,
    prior: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    """Trend forecast of revenue or output from complete recorded months."""
    storage, settings, vi = state.storage, state.settings, body.language == "vi"
    checked = validate_choices(raw, body.question)
    if checked.missing_fields:
        return 200, response(
            "needs_clarification",
            clarification_text(checked, body.language),
            request_id,
            conversation_id,
        )
    metric = raw.metric_id or ""
    if metric not in {"revenue", "production_output"}:
        text_out = (
            "Tôi chỉ dự báo được doanh thu hoặc sản lượng (các tỷ lệ không cộng dồn "
            "nên không dự báo theo cách này). Bạn muốn dự báo chỉ số nào?"
            if vi
            else "I can forecast revenue or production output only (ratios do not add "
            "up, so this method does not apply). Which one do you want?"
        )
        return 200, response(
            "needs_clarification", text_out, request_id, conversation_id
        )
    last_day = calendar.monthrange(anchor.year, anchor.month)[1]
    end = (
        anchor + timedelta(days=1) if anchor.day == last_day else anchor.replace(day=1)
    )
    start = month_start(end, -36)  # only complete months are history
    intent = Intent.model_validate(
        {
            "metric_id": metric,
            "dimension": "month",
            "period": "explicit",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "factory_id": checked.factory_id,
            "territory": checked.territory if metric == "revenue" else None,
            "limit": 100,
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
        }
    )
    try:
        authorize(intent, user["role"])
        check_definition(intent, state.dictionary)
        plan = validate(
            build(intent, user["role"], anchor),
            intent,
            user["role"],
            trusted_template=True,
        )
    except PermissionError:
        raise
    except ValueError as error:
        return 200, response(
            "needs_clarification",
            friendly(error, body.language),
            request_id,
            conversation_id,
        )
    rows = run_query(state.warehouse, plan, budget)
    months = [str(r["month"]) for r in rows]
    series = [float(r[metric]) for r in rows]
    wanted = raw.horizon_months or 6
    horizon, capped = min(wanted, 12), wanted > 12
    try:
        if not contiguous(months):
            raise ForecastRefused("gaps")
        result = make_forecast(
            series, horizon, settings.forecast_min_months, settings.forecast_max_mape
        )
    except ForecastRefused as refusal:
        facts = refusal.facts
        miss = float(facts.get("mape", 0)) * 100
        limit = float(facts.get("limit", 0)) * 100
        why = {
            "history": (
                f"Chỉ có {facts.get('have')} tháng lịch sử đầy đủ, cần ít nhất "
                f"{facts.get('need')}.",
                f"Only {facts.get('have')} complete months exist; at least "
                f"{facts.get('need')} are needed.",
            ),
            "error": (
                f"Phương pháp tốt nhất vẫn lệch {miss:.0f}% khi kiểm thử trên 6 tháng "
                f"gần nhất (ngưỡng {limit:.0f}%), nên số dự báo không đáng tin.",
                f"Even the best method missed the last 6 months by {miss:.0f}% "
                f"(limit {limit:.0f}%), so a forecast is not reliable.",
            ),
            "gaps": (
                "Chuỗi tháng có khoảng trống nên không dự báo được.",
                "The monthly series has gaps, so it cannot be forecast.",
            ),
            "zero_history": (
                "Lịch sử toàn bằng 0 nên không dự báo được.",
                "The history is all zero, so it cannot be forecast.",
            ),
        }[refusal.reason][0 if vi else 1]
        text_out = (
            f"Tôi không đưa ra dự báo: {why} Bạn có thể xem xu hướng lịch sử thay thế."
            if vi
            else f"I am not offering a forecast: {why} You can look at the historical "
            "trend instead."
        )
        return 200, response(
            "needs_clarification", text_out, request_id, conversation_id
        )
    shown = list(zip(months, series))[-12:]
    future = next_months(end, horizon)
    table = [
        {
            "month": m,
            "actual": f"{v:.2f}",
            "forecast": None,
            "lower": None,
            "upper": None,
        }
        for m, v in shown
    ] + [
        {
            "month": d.isoformat(),
            "actual": None,
            "forecast": f"{result.values[i]:.2f}",
            "lower": f"{result.lower[i]:.2f}",
            "upper": f"{result.upper[i]:.2f}",
        }
        for i, d in enumerate(future)
    ]
    viz = validate_viz(
        VizConfig(type="line", x="month", y=["actual", "forecast"], series=None), table
    )
    name = vocabulary.get().metric_label(metric, body.language).lower()
    total = sum(result.values)
    first_m, last_m = future[0], future[-1]
    trend = (
        (
            f" Xu hướng {'tăng' if result.slope >= 0 else 'giảm'} khoảng "
            f"{abs(result.slope):,.0f} mỗi tháng."
            if vi
            else f" The trend is {'up' if result.slope >= 0 else 'down'} about "
            f"{abs(result.slope):,.0f} per month."
        )
        if result.method == "linear_trend"
        else ""
    )
    method = {
        "linear_trend": ("đường xu hướng tuyến tính", "a straight-line trend"),
        "recent_average": ("trung bình 6 tháng gần nhất", "the last-six-month average"),
    }[result.method][0 if vi else 1]
    cap_note = (
        (" (đã giới hạn 12 tháng)" if vi else " (limited to 12 months)")
        if capped
        else ""
    )
    answer_text = (
        f"Dự báo, không phải số liệu đã ghi nhận: {name} từ {first_m:%m/%Y} đến "
        f"{last_m:%m/%Y}{cap_note} khoảng {total:,.0f} tổng cộng, mỗi tháng trong "
        f"khoảng {min(result.lower):,.0f} đến {max(result.upper):,.0f} (tin cậy 95%)."
        f"{trend} Dùng {method} trên {result.history_months} tháng đã ghi nhận; sai số "
        f"kiểm thử trên 6 tháng gần nhất là {result.backtest_mape * 100:.0f}%."
        if vi
        else f"Forecast, not recorded data: {name} from {first_m:%Y-%m} to "
        f"{last_m:%Y-%m}{cap_note} about {total:,.0f} in total, each month between "
        f"{min(result.lower):,.0f} and {max(result.upper):,.0f} (95% interval)."
        f"{trend} Uses {method} on {result.history_months} recorded months; the error "
        f"on the last 6 months was {result.backtest_mape * 100:.0f}%."
    )
    payload = response("ok", "Results found", request_id, conversation_id)
    payload.update(
        table=table,
        viz_config=viz.model_dump(),
        chart_fallback=False,
        answer_text=answer_text,
        llm_calls=0,
        sources={
            "source": "Adventureworks",
            "sql": plan.sql,
            "parameters": {
                k: str(v) if isinstance(v, date) else v for k, v in plan.params.items()
            },
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "metric_versions": {metric: plan.version},
            "data_as_of": anchor.isoformat(),
            "anchor_source": state.readiness["anchor_source"],
            "references": [],
            "forecast": {
                "is_forecast": True,
                "method": result.method,
                "method_version": METHOD_VERSION,
                "history_months": result.history_months,
                "history_window": [start.isoformat(), end.isoformat()],
                "horizon_months": horizon,
                "backtest_mape": round(result.backtest_mape, 4),
                "interval": "point forecast ± 1.96 x residual sigma",
                "residual_sigma": round(result.residual_std, 2),
            },
        },
    )
    save_context(
        storage,
        conversation_id,
        user["id"],
        (prior or {}).get("slots") or {},
        None,
        next_turns(prior, body.question, None),
    )
    persist(
        storage,
        user["id"],
        body.question,
        metric,
        authorize(intent, user["role"]),
        payload,
    )
    return 200, payload


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
    audit_outcome: str | None = None
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
        follow = None if target else analysis_kind(body.question)
        if follow and not re.search(metric_words(), fold(body.question)):
            latest = latest_in_conversation(
                storage, user["id"], conversation_id, user["role"]
            )
            previous = (latest or {}).get("payload") or {}
            text_answer = (
                analyze(follow, previous["table"], latest["metric_id"], body.language)
                if latest
                and previous.get("table")
                and latest["metric_id"] in vocabulary.get().metrics
                else None
            )
            if text_answer and latest:
                result = response("ok", "Results found", request_id, conversation_id)
                result.update(
                    table=previous["table"],
                    sources=previous["sources"],
                    viz_config=TABLE.model_dump(),
                    chart_fallback=True,
                    answer_text=text_answer,
                    llm_calls=0,
                )
                save_context(
                    storage,
                    conversation_id,
                    user["id"],
                    (prior or {}).get("slots") or {},
                    None,
                    next_turns(prior, body.question, None),
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
                outcome = result["status"]
                return 200, result
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
        context = {
            "data_as_of": anchor.isoformat(),
            "slots": prior["slots"] if prior else {},
            "pending_question": prior.get("pending_question") if prior else None,
            "turns": (prior or {}).get("turns") or [],
        }
        raw = (
            local_intent(body.question) if settings.local_intent_enabled else None
        ) or client.interpret(body.question, context, budget)
        if raw.intent_type in {"chat", "metadata", "unsupported"}:
            outcome, result = run_dialogue(
                raw,
                body,
                user,
                state,
                budget,
                anchor,
                request_id,
                conversation_id,
                prior,
            )
            return 200, result
        if raw.intent_type == "forecast":
            try:
                status_code, result = run_forecast(
                    raw,
                    body,
                    user,
                    state,
                    budget,
                    anchor,
                    request_id,
                    conversation_id,
                    prior,
                )
            except PermissionError:
                outcome = "denied"
                return 403, response(
                    outcome,
                    "Data is outside your access scope",
                    request_id,
                    conversation_id,
                )
            outcome = result["status"]
            return status_code, result
        intent = merged_intent(raw, prior, body.question)
        if (
            intent.dimension == "none"
            and intent.series_dimension == "none"
            and vocabulary.get().unrecognised_breakdown(fold(body.question))
        ):
            # A "by X" the dictionary does not know: never answer as if it were absent.
            outcome, result = run_dialogue(
                intent.model_copy(update={"intent_type": "unsupported"}),
                body,
                user,
                state,
                budget,
                anchor,
                request_id,
                conversation_id,
                prior,
            )
            return 200, result
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
                question = friendly(error, body.language)
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
        except PermissionError as error:
            if isinstance(error, SQLPolicyError) and not isinstance(
                error, SQLScopeError
            ):
                # A proposal that breaks a business rule is not an access failure.
                logger.warning(
                    "Request %s SQL proposal rejected: %s", request_id, error
                )
                audit_outcome = "policy_violation"  # recorded, not an access denial
                outcome = "needs_clarification"
                question = (
                    "Tôi chưa tạo được truy vấn đã được kiểm chứng cho yêu cầu này. "
                    "Hãy thử hỏi theo tháng, quý hoặc năm, hoặc diễn đạt lại."
                    if body.language == "vi"
                    else "I could not build a verified query for this request. Try "
                    "asking by month, quarter or year, or rephrase it."
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
            outcome = "denied"
            return 403, response(
                outcome,
                "Data is outside your access scope",
                request_id,
                conversation_id,
            )
        except ValueError as error:
            outcome = "needs_clarification"
            question = friendly(error, body.language)
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
        if outcome == "denied" or audit_outcome:
            audit(storage, user["id"], request_id, audit_outcome or outcome, metric_id)
