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

from app.ai.budget import BudgetExceeded, RequestBudget
from app.ai.client import FakeLLM, Intent, LLMBusy, dimension_ids
from app.conversation.dialogue import (
    chat_intent,
    fallback,
    last_answer,
    reply_from_metadata,
    small_talk,
)
from app.conversation.intent import (
    WHICH,
    chosen_option,
    clarification_text,
    first_clause,
    first_metric,
    first_task_text,
    follow_up_intent,
    local_comparison_intent,
    local_intent,
    local_unsupported,
    merged_intent,
    next_deferred,
    resolve_corrections,
    resume_pending,
    split_tasks,
    unstick,
    validate_choices,
)
from app.conversation.service import (
    get_context,
    next_turns,
    remember_answer,
    save_context,
)
from app.core.dates import (
    COMPARE_WORDS,
    FORMAT_REQUEST,
    GROWTH,
    WHY,
    Period,
    compares_two_periods,
    conflicting_years,
    date_hints,
    impossible_date,
    is_share_question,
    mentions_time,
    metric_words,
    month_start,
    named_periods,
    shifted_period,
    single_dimension,
)
from app.core.text import fold
from app.history.service import audit, latest_in_conversation
from app.history.service import save as save_result
from app.metadata import vocabulary
from app.presentation.analysis import (
    analysis_kind,
    analyze,
    fresh_request,
)
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
from app.query.comparison import (
    GROUP_COLUMN,
    MAX_PERIODS,
    adjacent,
    label,
    relative_pair,
    shift_pair,
    side_by_side,
    with_earlier_period,
)
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
BUSY_MESSAGE = "AI service is busy"
NOTE_STATUSES = {"ok", "no_data", "needs_clarification"}
MAX_TASKS = 5  # requests answered from one message
DATA_KINDS = {"metric_query", "comparison", "trend", "ranking", "needs_clarification"}


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
    "intent_type",
    "horizon_months",
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
    known = set(vocabulary.get().ids("factory").values())
    for name in SLOT_FIELDS:
        value = getattr(intent, name)
        if name == "intent_type" and value != "forecast":
            continue
        if value in (None, "none") or (
            name == "factory_id" and known and value not in known
        ):
            continue
        slots[name] = value
    if intent.dimension == "factory":  # "which factory ...": every factory, no filter
        slots.pop("factory_id", None)
    if intent.dimension == "sales_territory":
        slots.pop("territory", None)
    return slots


def shown_members(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Territories and factories on screen, so "those regions" can be resolved."""
    shown: dict[str, list[str]] = {}
    for key in ("territory", "factory"):
        names = [str(r[key]) for r in rows if r.get(key)]
        if 1 < len(names) <= 10:
            shown[key] = names
    return shown


def note_deferred(result: dict[str, Any], tasks: list[str], language: str) -> None:
    """Say plainly which requests were not run, so none is dropped without a word."""
    if not tasks or result.get("status") not in NOTE_STATUSES:
        return
    listed = "; ".join(tasks)
    for metric in vocabulary.get().metrics.values():  # ids are not for people
        listed = listed.replace(metric.id, metric.label(language).lower())
    note = (
        f"Chưa thực hiện thêm: {listed} (mỗi lần tối đa {MAX_TASKS} yêu cầu). "
        "Hãy hỏi tiếp để tôi làm."
        if language == "vi"
        else f"Not run: {listed} (at most {MAX_TASKS} requests per message). "
        "Ask again and I will."
    )
    key = "message" if result["status"] == "needs_clarification" else "answer_text"
    base = result.get(key)
    result[key] = f"{base} {note}" if base else note


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


def horizon_to(question: str, first: date) -> int | None:
    """Months from the first forecast month to the end of a period the user named.

    "Forecast for 2026" means through December 2026; None when no period is named."""
    named = date_hints(question).get("end_date")
    if not named:
        return None
    last = date.fromisoformat(named) - timedelta(days=1)
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    return months if months > 0 else None


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
    canned: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Answer about the data itself, or decline what the system cannot serve."""
    limitation = raw.intent_type == "unsupported"
    mode = "limitation" if limitation else "answer"
    if limitation and canned is None and re.search(WHY, fold(body.question)):
        # "Why did it fall?" always gets the same plain answer, whatever the model says.
        canned = (
            "Tôi chỉ đưa ra số liệu, chưa giải thích được nguyên nhân. "
            if body.language == "vi"
            else "I can give the figures but not explain the reasons. "
        ) + fallback("answer", body.language, user["role"])
    previous = last_answer(
        latest_in_conversation(state.storage, user["id"], conversation_id, user["role"])
    )
    text_out = canned or reply_from_metadata(
        state.llm,
        body.question,
        user["role"],
        mode,
        anchor.isoformat(),
        budget,
        previous,
        body.language,
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

    def ask_again(text_out: str) -> tuple[int, dict[str, Any]]:
        """Ask one more detail and remember that a forecast is what is pending."""
        text_out = unstick(text_out, prior, body.language)
        save_context(
            storage,
            conversation_id,
            user["id"],
            carry_slots(prior, raw),
            text_out,
            next_turns(prior, body.question, text_out),
        )
        return 200, response(
            "needs_clarification", text_out, request_id, conversation_id
        )

    checked = validate_choices(raw, body.question)
    if checked.missing_fields:
        return ask_again(clarification_text(checked, body.language))
    metric = raw.metric_id or ""
    if metric not in {"revenue", "production_output"}:
        text_out = (
            "Tôi chỉ dự báo được doanh thu hoặc sản lượng (các tỷ lệ không cộng dồn "
            "nên không dự báo theo cách này). Bạn muốn dự báo chỉ số nào?"
            if vi
            else "I can forecast revenue or production output only (ratios do not add "
            "up, so this method does not apply). Which one do you want?"
        )
        return ask_again(text_out)
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
        return ask_again(friendly(error, body.language))
    rows = run_query(state.warehouse, plan, budget)
    months = [str(r["month"]) for r in rows]
    series = [float(r[metric]) for r in rows]
    named = horizon_to(body.question, end)
    if named is not None and named > 12:
        # A period far ahead is not answered with a forecast of nearer months.
        last = next_months(end, 12)[-1]
        return ask_again(
            f"Tôi chỉ dự báo tối đa 12 tháng tới (đến {last:%m/%Y}), nên chưa dự báo "
            "được kỳ bạn nêu. Hãy hỏi một kỳ trong khoảng đó, ví dụ “dự báo doanh "
            "thu 6 tháng tới”."
            if vi
            else f"I forecast at most 12 months ahead (to {last:%Y-%m}), so I cannot "
            "forecast the period you name. Ask for one inside that range, for "
            "example “forecast revenue for the next 6 months”."
        )
    wanted = raw.horizon_months or named or 6
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


def scope_of(intent: Intent) -> str:
    """The territories or factory a result is limited to, for the answer text."""
    if intent.territory:
        return ", ".join(intent.territory.split("|"))
    names = [
        name
        for name, number in vocabulary.get().ids("factory").items()
        if number == intent.factory_id
    ]
    return names[0] if names else ""


def run_comparison(
    intent: Intent,
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
    budget: RequestBudget,
    anchor: date,
    request_id: str,
    conversation_id: str,
    prior: dict[str, Any] | None,
    periods: list[Period],
) -> tuple[int, dict[str, Any]]:
    """One metric across the periods named in the question, side by side."""
    storage, vi = state.storage, body.language == "vi"

    def ask_again(text_out: str) -> tuple[int, dict[str, Any]]:
        text_out = unstick(text_out, prior, body.language)
        save_context(
            storage,
            conversation_id,
            user["id"],
            carry_slots(prior, intent),
            text_out,
            next_turns(prior, body.question, text_out),
        )
        return 200, response(
            "needs_clarification", text_out, request_id, conversation_id
        )

    checked = validate_choices(intent, body.question)
    if not intent.metric_id or {"factory_id", "territory"} & set(
        checked.missing_fields
    ):
        return ask_again(clarification_text(checked, body.language))
    intent = checked.model_copy(
        update={"needs_clarification": False, "missing_fields": []}
    )
    if len(periods) > MAX_PERIODS or len({p[0] for p in periods}) > 1:
        return ask_again(
            "Hãy so sánh tối đa 4 kỳ cùng loại: cùng là năm, cùng là quý hoặc cùng "
            "là tháng."
            if vi
            else "Please compare up to 4 periods of the same kind: all years, all "
            "quarters or all months."
        )
    territories = intent.territory.split("|") if intent.territory else []
    group = GROUP_COLUMN.get(intent.dimension)
    if (intent.dimension == "sales_territory" and len(territories) == 1) or (
        intent.dimension == "factory" and intent.factory_id is not None
    ):
        group = None  # one member named: a filter, not a breakdown
    base = intent.model_copy(
        update={
            "dimension": intent.dimension if group else "none",
            "series_dimension": "none",
            "period": "explicit",
            "limit": 100,
        }
    )
    factory_names = [
        name
        for name, number in vocabulary.get().ids("factory").items()
        if number == intent.factory_id
    ]
    results: list[tuple[str, list[dict[str, Any]]]] = []
    plans = []
    for period in periods:
        one = base.model_copy(
            update={
                "start_date": period[1].isoformat(),
                "end_date": period[2].isoformat(),
            }
        )
        try:
            authorize(one, user["role"])
            check_definition(one, state.dictionary)
            plan, _, _ = route_query(
                body.question, one, user["role"], anchor, state, budget
            )
        except ValueError as error:  # PermissionError propagates to the caller
            return ask_again(friendly(error, body.language))
        rows = run_query(state.warehouse, plan, budget)
        results.append((label(period, body.language), rows))
        plans.append(plan)
    table, answer_text = side_by_side(
        intent.metric_id,
        results,
        group,
        "" if group else ", ".join(territories + factory_names),
        body.language,
    )
    payload = response(
        "ok" if table else "no_data",
        "Results found" if table else "No data for these periods",
        request_id,
        conversation_id,
    )
    payload.update(table=table, answer_text=answer_text, llm_calls=budget.calls)
    payload["sources"] = {
        "source": "Adventureworks",
        "sql": "\n\n".join(plan.sql for plan in plans),
        "parameters": {
            k: str(v) if isinstance(v, date) else v for k, v in plans[-1].params.items()
        }
        | {"start": periods[0][1].isoformat(), "end": periods[-1][2].isoformat()},
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "metric_versions": {intent.metric_id: plans[-1].version},
        "data_as_of": anchor.isoformat(),
        "anchor_source": state.readiness["anchor_source"],
        "references": [],
    }
    if table:
        wanted = requested_chart(body.question)
        if group and not wanted:
            viz = validate_viz(
                VizConfig(
                    type="bar",
                    x=group,
                    y=[name for name, found in results if found],
                    series=None,
                ),
                table,
            )
            payload["viz_config"], payload["chart_fallback"] = viz.model_dump(), False
        else:
            viz_dict, note = reshaped(wanted or "bar", table, body.language)
            payload["viz_config"] = viz_dict
            payload["chart_fallback"] = viz_dict["type"] == "table"
            if wanted and viz_dict["type"] != wanted:
                payload["answer_text"] = f"{answer_text} {note}"
    last = periods[-1]
    save_context(
        storage,
        conversation_id,
        user["id"],
        {
            **base.model_dump(),
            "start_date": last[1].isoformat(),
            "end_date": last[2].isoformat(),
            "shown": shown_members(table),
        },
        None,
        next_turns(prior, body.question, None),
    )
    persist(
        storage,
        user["id"],
        body.question,
        intent.metric_id,
        authorize(base, user["role"]),
        payload,
    )
    return 200, payload


def answer(
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
) -> tuple[int, dict[str, Any]]:
    """Run every request in the message and show each result, in order.

    The first result is the response itself; the others follow in `parts`."""
    carry: dict[str, Any] = {}
    status, first = answer_one(body, user, state, carry)
    queue = [t.strip() for t in carry.get("deferred", []) if t.strip()]
    if not queue or first.get("status") == "technical_failure":
        return status, first
    storage, uid, conversation = state.storage, user["id"], first["conversation_id"]
    kept = get_context(storage, conversation, uid)  # the first request's own state
    seen = {fold(carry.get("head") or body.question), *(fold(t) for t in queue)}
    parts: list[dict[str, Any]] = []
    while queue and len(parts) < MAX_TASKS - 1:
        task = queue.pop(0)
        later: dict[str, Any] = {}
        known = get_context(storage, conversation, uid) is not None
        try:
            _, part = answer_one(
                AskRequest(
                    question=task[:1000],
                    conversation_id=conversation if known else None,
                    language=body.language,
                ),
                user,
                state,
                later,
            )
        except HTTPException:
            continue
        conversation = part["conversation_id"]
        for extra in later.get("deferred", []):
            if extra.strip() and fold(extra) not in seen:
                seen.add(fold(extra))
                queue.append(extra.strip())
        part["title"] = f"{len(parts) + 2}. {task[:1].upper()}{task[1:]}"
        parts.append(part)
        if part["status"] == "technical_failure":
            break
    if kept is not None and first["status"] == "needs_clarification":
        # The first request is still waiting for its answer: put its state back.
        save_context(
            storage,
            first["conversation_id"],
            uid,
            kept["slots"],
            kept["pending_question"],
            kept["turns"],
        )
    else:
        first["conversation_id"] = conversation
    head = carry.get("head") or first_clause(body.question)
    first["title"] = f"1. {head[:1].upper()}{head[1:]}"
    first["parts"] = parts
    note_deferred(first, queue, body.language)
    return 200, first


def answer_one(
    body: AskRequest,
    user: dict[str, Any],
    state: Any,
    carry: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """One request. `carry["deferred"]` receives the requests left for later."""
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
                # An id with no saved state (its first answer was a refusal, or it is
                # not this user's): start a new conversation instead of failing.
                conversation_id = str(uuid4())
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
            named = vocabulary.get().match_members(fold(body.question))
            window = (previous.get("sources") or {}).get("parameters") or {}
            # A table cut down to some territories is not the whole: a share of "all
            # territories" must be queried, not read from it.
            partial = follow == "share" and any(
                k == "territory" or k.startswith("territory_") for k in window
            )
            share = (
                share_text(
                    previous.get("table") or [],
                    named.get("sales_territory") or [],
                    date.fromisoformat(str(window["start"])),
                    date.fromisoformat(str(window["end"])),
                    body.language,
                )
                if follow == "share"
                and named.get("sales_territory")
                and window
                and not partial
                else None
            )
            shown = {fold(n) for names in named.values() for n in names}
            picked = [
                row
                for row in previous.get("table") or []
                if shown & {fold(str(v)) for v in row.values()}
            ]
            text_answer = share or (
                analyze(
                    follow,
                    picked if len(picked) >= 2 else previous["table"],
                    latest["metric_id"],
                    body.language,
                )
                if latest
                and not partial
                and previous.get("table")
                and latest["metric_id"] in vocabulary.get().metrics
                and not fresh_request(
                    body.question, previous["table"], latest["metric_id"]
                )
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
            asked = analysis_kind(body.question)  # "which is highest, draw a pie"
            if asked and latest["metric_id"] in vocabulary.get().metrics:
                reading = analyze(
                    asked, previous["table"], latest["metric_id"], body.language
                )
                note = f"{reading} {note}" if reading else note
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
        # A short reply that picks an offered option stands for that option's text.
        asked = (
            chosen_option(body.question, (prior or {}).get("pending_question"))
            or body.question
        )
        later, waiting = next_deferred(body.question, prior)
        if later:
            asked = later
        # A message with several requests: the model reads the first one only.
        if not later:
            asked = resolve_corrections(asked)
        tasks = [asked] if later else split_tasks(asked)
        first = tasks[0]
        canned = (
            None
            if later
            else small_talk(
                first,
                body.language,
                user["role"],
                bool((prior or {}).get("pending_question")),
            )
        )
        raw = (
            chat_intent()
            if canned
            else local_unsupported(first)
            or local_comparison_intent(first)
            or (local_intent(first) if settings.local_intent_enabled else None)
        )
        if raw is None:
            try:
                raw = client.interpret(
                    first if len(tasks) > 1 else asked, context, budget
                )
            except (LLMBusy, httpx.HTTPError):
                # Every AI key is resting or slow: plain wording needs no model.
                raw = local_intent(first)
                if raw is None and (prior or {}).get("slots"):
                    # the earlier turn fills in the rest
                    raw = follow_up_intent(first, prior["slots"])
                if raw is None:
                    raise
        if later:
            raw = raw.model_copy(update={"deferred_requests": waiting})
        else:
            extra = raw.deferred_requests or tasks[1:]
            if extra:  # several tasks: run the first one, list the rest
                update: dict[str, Any] = {"deferred_requests": extra}
                if raw.intent_type in DATA_KINDS and (
                    raw.metric_id is None or raw.needs_clarification
                ):
                    # It asked "which one?" though the first task is plain.
                    update.update(
                        metric_id=raw.metric_id or first_metric(body.question),
                        needs_clarification=False,
                        clarification_question=None,
                        missing_fields=[],
                    )
                raw = raw.model_copy(update=update)
        raw = resume_pending(raw, prior)
        # From here on the question is the first request alone: what it says about
        # periods, chart types or breakdowns is not read from the others.
        head = (
            first
            if len(tasks) > 1
            else first_task_text(asked, list(raw.deferred_requests))
        )
        body = body.model_copy(update={"question": head})
        if carry is not None:
            carry["deferred"] = list(raw.deferred_requests)
            carry["head"] = head
        # "Compare 2024 with 2030" is a comparison, not a forecast request.
        comparing = compares_two_periods(body.question) and not re.search(
            r"\b(?:du bao|forecast|predict)\b", fold(body.question)
        )
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
                canned,
            )
            return 200, result
        if raw.intent_type == "forecast" and not comparing:
            # "Was that based on total revenue?" names no period of its own:
            # it asks about the forecast just given, so do not compute again.
            repeated = not mentions_time(body.question) and (
                (
                    (
                        latest_in_conversation(
                            storage, user["id"], conversation_id, user["role"]
                        )
                        or {}
                    ).get("payload")
                    or {}
                )
                .get("sources", {})
                .get("forecast")
            )
            if repeated:
                outcome, result = run_dialogue(
                    raw.model_copy(update={"intent_type": "metadata"}),
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
        if moved := shifted_period(
            body.question, (prior or {}).get("slots") or {}, anchor
        ):
            # "Cùng kỳ năm trước", "quý trước đó nữa": the earlier period, moved.
            intent = intent.model_copy(
                update={
                    "period": "explicit",
                    "start_date": moved[0].isoformat(),
                    "end_date": moved[1].isoformat(),
                    "needs_clarification": False,
                    "missing_fields": [],
                    "clarification_question": None,
                }
            )
        if bad := impossible_date(body.question):
            outcome = "needs_clarification"
            question = (
                f"“{bad}” không có trong lịch. Bạn muốn ngày hoặc tháng nào? Ví dụ: "
                "tháng 3 năm 2025 hoặc 28/02/2024."
                if body.language == "vi"
                else f"“{bad}” is not a date in the calendar. Which day or month do "
                "you mean? For example: March 2025 or 2024-02-28."
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
        if not comparing and (clash := conflicting_years(body.question)):
            outcome = "needs_clarification"
            question = (
                f"Bạn nhắc tới cả năm {clash[0]} và năm {clash[1]}. "
                "Bạn muốn xem năm nào?"
                if body.language == "vi"
                else f"You mention both {clash[0]} and {clash[1]}. "
                "Which year do you mean?"
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
        growth_note = None
        periods = named_periods(body.question) if comparing else []
        if not periods and re.search(COMPARE_WORDS, fold(body.question)):
            # "So sánh với 2023" after a 2024 answer: the period on screen and 2023.
            slots = (prior or {}).get("slots") or {}
            periods = (
                with_earlier_period(named_periods(body.question), slots)
                or relative_pair(body.question, slots, anchor)
                or shift_pair(body.question, slots, anchor)
            )
        if len(periods) >= 2:
            if intent.dimension in {"month", "day", "week"} and (
                single_dimension(fold(body.question)) != intent.dimension
            ):  # the model added a by-month view nobody asked for
                intent = intent.model_copy(update={"dimension": "none"})
            if intent.dimension == "none" and "|" in (intent.territory or ""):
                intent = intent.model_copy(update={"dimension": "sales_territory"})
            if intent.metric_id == "sales_growth":
                intent = intent.model_copy(update={"metric_id": "revenue"})
            if intent.dimension in {"none", *GROUP_COLUMN}:
                metric_id = intent.metric_id
                try:
                    status_code, result = run_comparison(
                        intent,
                        body,
                        user,
                        state,
                        budget,
                        anchor,
                        request_id,
                        conversation_id,
                        prior,
                        periods,
                    )
                except PermissionError:
                    outcome = "denied"
                    return 403, response(
                        outcome,
                        "Data is outside your access scope",
                        request_id,
                        conversation_id,
                    )
                outcome = result["status"]  # "and 2030" is not a second task
                return status_code, result
            if adjacent(periods):
                # By month (or another breakdown) over the whole stretch: one query.
                intent = intent.model_copy(
                    update={
                        "period": "explicit",
                        "start_date": periods[0][1].isoformat(),
                        "end_date": periods[-1][2].isoformat(),
                    }
                )
                growth_note = (
                    "Đây là kết quả cho cả khoảng thời gian liền nhau bạn nêu."
                    if body.language == "vi"
                    else "This covers the whole stretch of consecutive periods named."
                )
            else:
                intent = intent.model_copy(
                    update={
                        "needs_clarification": True,
                        "clarification_question": (
                            "Với cách chia này tôi chỉ xem được một khoảng liền nhau. "
                            "Hãy hỏi từng kỳ, hoặc so sánh tổng của các kỳ."
                            if body.language == "vi"
                            else "With that breakdown I can only show one continuous "
                            "stretch. Ask each period separately, or compare totals."
                        ),
                    }
                )
        if intent.metric_id == "sales_growth" and intent.period not in {
            "last_month",
            "last_quarter",
            "this_month",
            "this_quarter",
        }:
            # Trend of the base metric by month; a breakdown becomes the series.
            split = intent.dimension not in {"none", "month"}
            intent = intent.model_copy(
                update={
                    "metric_id": "revenue",
                    "dimension": "month",
                    "series_dimension": (
                        intent.dimension if split else intent.series_dimension
                    ),
                }
            )
            growth_note = (
                "Tăng trưởng theo kỳ liền trước chỉ tính cho tháng hoặc quý gần "
                "nhất, nên đây là doanh thu theo tháng để thấy xu hướng."
                if body.language == "vi"
                else "Growth against the previous period exists only for the latest "
                "month or quarter, so this is revenue by month to show the trend."
            )
        by_quarter = re.search(
            r"\b(?:theo|by|moi|hang|each|per)\s+(?:quy|quarter)\b", fold(body.question)
        )
        if by_quarter and (
            intent.dimension not in dimension_ids()
            or intent.series_dimension not in dimension_ids()
            or intent.dimension == "none"
        ):
            intent = intent.model_copy(
                update={"dimension": "month", "series_dimension": "none"}
            )
        if by_quarter and intent.dimension == "month":
            growth_note = (
                "Chưa chia theo quý được nên đây là kết quả theo tháng."
                if body.language == "vi"
                else "Quarterly breakdown is not available, so this is by month."
            )
        if re.search(FORMAT_REQUEST, fold(body.question)):
            unit = (
                "Tôi chưa làm tròn hoặc đổi đơn vị (triệu, tỷ, ...); số liệu hiển thị "
                "đúng như trong dữ liệu."
                if body.language == "vi"
                else "I do not round or change units yet; figures are shown as stored."
            )
            growth_note = f"{growth_note} {unit}" if growth_note else unit
        if re.search(WHY, fold(body.question)):
            reason = (
                "Tôi chỉ đưa ra số liệu, chưa giải thích được nguyên nhân."
                if body.language == "vi"
                else "I can give the figures but not explain the reasons."
            )
            growth_note = f"{growth_note} {reason}" if growth_note else reason
        if WHICH.search(fold(body.question)) and intent.dimension != "none":
            # "Which factory is best?" shows every factory so the answer can be checked.
            intent = intent.model_copy(update={"limit": max(intent.limit, 100)})
        if intent.dimension in {"day", "week", "month"} and intent.limit < 250:
            # Rows come in date order: "the highest month" cannot be cut to one row.
            intent = intent.model_copy(update={"limit": 250})
        if intent.series_dimension != "none" and intent.limit < 250:
            # A stacked chart needs every group: the default cap would cut it short.
            intent = intent.model_copy(update={"limit": 250})
        if re.search(GROWTH, fold(body.question)) and intent.metric_id not in {
            "revenue",
            "sales_growth",
            None,
        }:
            outcome = "needs_clarification"
            question = (
                "So sánh với kỳ trước chỉ có cho doanh thu. Với chỉ số này hãy hỏi "
                "từng kỳ, ví dụ “tháng này” rồi “tháng trước”."
                if body.language == "vi"
                else "Comparison with the previous period exists only for revenue. "
                "For this metric ask each period, for example “this month” and "
                "then “last month”."
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
                question = unstick(friendly(error, body.language), prior, body.language)
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
            question = unstick(
                clarification_text(intent, body.language), prior, body.language
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
            question = unstick(friendly(error, body.language), prior, body.language)
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
            {**intent.model_dump(), "shown": shown_members(rows)},
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
                or factual(
                    plan.metric_id,
                    rows,
                    plan.start,
                    plan.end,
                    body.language,
                    scope_of(intent),
                )
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
                    body.question,
                    rows,
                    state,
                    budget,
                    intent.metric_id,
                    intent.limit < 100,
                )
            if settings.send_results_to_llm and settings.external_results_enabled:
                try:
                    proposed = client.summarize(body.question, rows, budget)
                    if numbers_match(proposed, rows):
                        result["answer_text"] = proposed
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
                    logger.warning("Request %s summary fell back", request_id)
        result["llm_calls"] = budget.calls
        if growth_note:
            result["answer_text"] = f"{result['answer_text']} {growth_note}"
        persist(
            storage,
            user["id"],
            body.question,
            plan.metric_id,
            authorize(intent, user["role"]),
            result,
        )
        if rows and result.get("answer_text"):
            try:
                remember_answer(
                    storage, conversation_id, user["id"], result["answer_text"]
                )
            except SQLAlchemyError:
                logger.warning("Request %s answer not kept in context", request_id)
        return 200, result
    except HTTPException:
        raise
    except BudgetExceeded:
        logger.warning("Request %s: model call budget spent", request_id)
        outcome = "needs_clarification"
        # Keep the conversation: the next message continues it.
        earlier = locals().get("prior") or {}
        try:
            save_context(
                storage,
                conversation_id,
                user["id"],
                earlier.get("slots") or {},
                None,
                earlier.get("turns") or [],
            )
        except SQLAlchemyError:
            logger.warning("Request %s: context not saved", request_id)
        return 200, response(
            outcome,
            "Tôi chưa hiểu rõ câu hỏi này sau nhiều lần thử. Hãy diễn đạt lại ngắn "
            "gọn hơn, gồm chỉ số, khoảng thời gian và cách chia nhóm."
            if body.language == "vi"
            else "I could not make sense of this after several tries. Please rephrase "
            "it briefly with the metric, the period and the breakdown.",
            request_id,
            conversation_id,
        )
    except (LLMBusy, httpx.TimeoutException):
        logger.warning("Request %s: AI provider rate limited or slow", request_id)
        return 503, response(
            "technical_failure", BUSY_MESSAGE, request_id, conversation_id
        )
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
