"""A compound message runs a forecast; the forecast keeps its own metric and scope.

`run_forecast` and the deferred-task loop in `answer()` are only exercised together
here: `test_forecast.py` tests the forecast math alone, and the other orchestrator
tests call `carry_slots` directly. This drives the two through a fake LLM and a fake
warehouse/storage, the way a real compound message ("do X, then forecast Y") does.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.ai.client import FakeLLM, Intent
from app.core.config import Settings
from app.core.dates import month_start
from app.metadata.dictionary import load_dictionary
from app.presentation.contract import AskRequest
from app.query import orchestrator

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
USER = {"id": 1, "role": "manager"}


def intent(**changes: object) -> Intent:
    values = dict(
        metric_id=None,
        dimension="none",
        period=None,
        start_date=None,
        end_date=None,
        factory_id=None,
        territory=None,
        limit=100,
        needs_clarification=False,
        clarification_question=None,
        zero_scrap_only=False,
        missing_fields=[],
        series_dimension="none",
        intent_type="metric_query",
        horizon_months=None,
        deferred_requests=[],
    )
    values.update(changes)
    return Intent.model_validate(values)


class ConversationStore:
    """Stands in for the Postgres-backed chat_context/saved_results tables."""

    def __init__(self) -> None:
        self.contexts: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}


def fake_get_context(
    engine: ConversationStore, conversation_id: str, user_id: int
) -> dict[str, Any] | None:
    return engine.contexts.get(conversation_id)


def fake_save_context(
    engine: ConversationStore,
    conversation_id: str,
    user_id: int,
    slots: dict[str, Any],
    pending_question: str | None,
    turns: list[dict[str, str]] | None = None,
) -> None:
    engine.contexts[conversation_id] = {
        "slots": slots,
        "turns": turns or [],
        "pending_question": pending_question,
    }


def fake_save_result(
    engine: ConversationStore,
    user_id: int,
    question: str,
    metric_id: str,
    factory_id: int | None,
    payload: dict[str, Any],
    domain: str | None = None,
) -> str:
    result_id = str(uuid4())
    engine.results[payload["conversation_id"]] = {
        "id": result_id,
        "metric_id": metric_id,
        "domain": domain or "sales",
        "factory_id": factory_id,
        "payload": payload,
    }
    return result_id


def fake_latest_in_conversation(
    engine: ConversationStore, user_id: int, conversation_id: str, role: str
) -> dict[str, Any] | None:
    return engine.results.get(conversation_id)


def fake_remember_answer(*args: object) -> None:
    return None


def fake_audit(*args: object) -> None:
    return None


def fake_run_query(engine: object, plan: Any, budget: object) -> list[dict[str, Any]]:
    """A straight-line monthly history for the forecast; one row otherwise."""
    if 'AS "month"' in plan.sql:
        months = []
        day = plan.start
        while day < plan.end:
            months.append(day)
            day = month_start(day, 1)
        return [
            {"month": m.isoformat(), plan.metric_id: f"{100000.0 + 1000 * i:.2f}"}
            for i, m in enumerate(months)
        ]
    return [{"revenue": "50000.00", "sample_count": "12"}]


def make_state(llm: FakeLLM) -> SimpleNamespace:
    settings = Settings(
        _env_file=None,
        warehouse_password="x",
        app_db_password="x",
        local_intent_enabled=False,
        external_metadata_enabled=False,
    )
    return SimpleNamespace(
        settings=settings,
        llm=llm,
        storage=ConversationStore(),
        warehouse=object(),
        dictionary=load_dictionary(DATA_DIR),
        retriever=None,
        readiness={"data_as_of": "2025-06-29", "anchor_source": "test"},
    )


def patch_storage(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator, "get_context", fake_get_context)
    monkeypatch.setattr(orchestrator, "save_context", fake_save_context)
    monkeypatch.setattr(orchestrator, "save_result", fake_save_result)
    monkeypatch.setattr(
        orchestrator, "latest_in_conversation", fake_latest_in_conversation
    )
    monkeypatch.setattr(orchestrator, "remember_answer", fake_remember_answer)
    monkeypatch.setattr(orchestrator, "audit", fake_audit)
    monkeypatch.setattr(orchestrator, "run_query", fake_run_query)


def test_a_forecast_inside_a_compound_message_keeps_its_own_metric_and_scope(
    monkeypatch: Any,
) -> None:
    patch_storage(monkeypatch)
    task1 = "Revenue for Australia this month"
    task2 = "forecast production output for 3 months"
    llm = FakeLLM(
        {
            task1: intent(
                metric_id="revenue", period="this_month", territory="Australia"
            ),
            task2: intent(
                metric_id="production_output",
                intent_type="forecast",
                horizon_months=3,
            ),
        }
    )
    state = make_state(llm)
    body = AskRequest(question=f"{task1}, then {task2}", language="en")

    status, result = orchestrator.answer(body, USER, state)

    assert status == 200
    assert result["status"] == "ok"
    assert len(result["parts"]) == 1
    forecast_part = result["parts"][0]
    assert forecast_part["status"] == "ok"
    assert forecast_part["sources"]["forecast"]["is_forecast"] is True

    # The metric_query ran first and left revenue/Australia in the slots; the
    # forecast that ran after it must overwrite them with its own metric and
    # scope, not leave the earlier request's territory dangling on a metric
    # (production_output) that has no territory dimension at all.
    slots = state.storage.contexts[result["conversation_id"]]["slots"]
    assert slots["metric_id"] == "production_output"
    assert slots["horizon_months"] == 3
    assert "territory" not in slots


def test_a_follow_up_after_a_forecast_continues_its_metric_not_an_earlier_one(
    monkeypatch: Any,
) -> None:
    patch_storage(monkeypatch)
    task1 = "Revenue for Australia this month"
    task2 = "forecast production output for 3 months"
    follow_up = "3 months more"
    llm = FakeLLM(
        {
            task1: intent(
                metric_id="revenue", period="this_month", territory="Australia"
            ),
            task2: intent(
                metric_id="production_output",
                intent_type="forecast",
                horizon_months=3,
            ),
            follow_up: intent(
                metric_id="production_output",
                intent_type="forecast",
                horizon_months=3,
            ),
        }
    )
    state = make_state(llm)
    body = AskRequest(question=f"{task1}, then {task2}", language="en")
    _, first_result = orchestrator.answer(body, USER, state)
    conversation_id = first_result["conversation_id"]

    _, follow_up_result = orchestrator.answer_one(
        AskRequest(question=follow_up, conversation_id=conversation_id, language="en"),
        USER,
        state,
    )

    assert follow_up_result["status"] == "ok"
    # "3 months more" extends the 3-month forecast just given, not repeats it.
    assert follow_up_result["sources"]["forecast"]["horizon_months"] == 6
    slots = state.storage.contexts[conversation_id]["slots"]
    # A regression here would have reverted to "revenue" (the first request's
    # metric, two turns back) instead of staying on the forecast just given.
    assert slots["metric_id"] == "production_output"
    assert slots["horizon_months"] == 6
    assert "territory" not in slots


def test_requests_already_run_are_not_run_again_when_the_model_echoes_them(
    monkeypatch: Any,
) -> None:
    patch_storage(monkeypatch)
    task1 = "Revenue for Australia this month"
    task2 = "forecast production output for 3 months"
    later = "Which tables are there?"
    llm = FakeLLM(
        {
            task1: intent(
                metric_id="revenue", period="this_month", territory="Australia"
            ),
            task2: intent(
                metric_id="production_output",
                intent_type="forecast",
                horizon_months=3,
            ),
            # The model copies a request it saw in the conversation context.
            later: intent(intent_type="metadata", deferred_requests=[task2]),
        }
    )
    state = make_state(llm)
    _, first_result = orchestrator.answer(
        AskRequest(question=f"{task1}, then {task2}", language="en"), USER, state
    )
    conversation_id = first_result["conversation_id"]
    slots = state.storage.contexts[conversation_id]["slots"]
    assert not slots.get("deferred_requests")  # both requests ran

    _, result = orchestrator.answer(
        AskRequest(question=later, conversation_id=conversation_id, language="en"),
        USER,
        state,
    )

    assert not result.get("parts")


@pytest.mark.parametrize("model_kind", ["metadata", "forecast"])
def test_another_territory_after_a_forecast_is_forecast_too(
    monkeypatch: Any, model_kind: str
) -> None:
    patch_storage(monkeypatch)
    first = "forecast revenue for Germany for 6 months"
    follow_up = "what about France"
    answers = {
        first: intent(
            metric_id="revenue",
            intent_type="forecast",
            territory="Germany",
            horizon_months=6,
        ),
        follow_up: (
            intent(intent_type="metadata")
            if model_kind == "metadata"
            else intent(
                metric_id="revenue",
                intent_type="forecast",
                territory="France",
                horizon_months=6,
            )
        ),
    }
    state = make_state(FakeLLM(answers))
    _, first_result = orchestrator.answer_one(
        AskRequest(question=first, language="en"), USER, state
    )
    conversation_id = first_result["conversation_id"]

    _, result = orchestrator.answer_one(
        AskRequest(question=follow_up, conversation_id=conversation_id, language="en"),
        USER,
        state,
    )

    assert result["status"] == "ok"
    assert result["sources"]["forecast"]["horizon_months"] == 6
    assert result["sources"]["parameters"]["territory"] == "France"
    assert "France" in result["answer_text"]


def test_a_vietnamese_forecast_writes_numbers_and_scope_the_vietnamese_way(
    monkeypatch: Any,
) -> None:
    patch_storage(monkeypatch)
    question = "Dự báo doanh thu của Đức 6 tháng tới"
    llm = FakeLLM(
        {
            question: intent(
                metric_id="revenue",
                intent_type="forecast",
                territory="Germany",
                horizon_months=6,
            )
        }
    )
    state = make_state(llm)

    _, result = orchestrator.answer_one(
        AskRequest(question=question, language="vi"), USER, state
    )

    text = result["answer_text"]
    assert "Germany" in text
    assert re.search(r"\d\.\d{3}", text), text  # 1.234.567, as in the table
    assert not re.search(r"\d,\d{3}\b", text), text  # never 1,234,567
    assert "ETS(A,A,N)" in text and "AICc" in text  # the model is named


def test_a_refusal_names_the_scope_and_never_a_silly_percentage(
    monkeypatch: Any,
) -> None:
    patch_storage(monkeypatch)

    def last_month_almost_empty(engine: object, plan: Any, budget: object) -> Any:
        rows = fake_run_query(engine, plan, budget)
        rows[-1][plan.metric_id] = "3.00"  # a few dollars of stray orders
        return rows

    monkeypatch.setattr(orchestrator, "run_query", last_month_almost_empty)
    question = "Dự báo doanh thu của Central 3 tháng tới"
    llm = FakeLLM(
        {
            question: intent(
                metric_id="revenue",
                intent_type="forecast",
                territory="Central",
                horizon_months=3,
            )
        }
    )

    _, result = orchestrator.answer_one(
        AskRequest(question=question, language="vi"), USER, make_state(llm)
    )

    assert result["status"] == "needs_clarification"
    assert "doanh thu của Central" in result["message"]
    assert "hơn 100%" in result["message"], result["message"]
