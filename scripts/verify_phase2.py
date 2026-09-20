# ruff: noqa: E501
"""Offline Phase 2 golden run with FakeLLM and independent reference SQL."""

import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.client import FakeLLM, Intent  # noqa: E402
from app.conversation.intent import local_intent  # noqa: E402
from app.query.shortcuts import special_kind  # noqa: E402
from app.core.dates import month_start, resolve_period  # noqa: E402
from app.main import app  # noqa: E402


def intent_for(item: dict[str, Any]) -> Intent:
    number = int(item["id"][2:])
    dims = {
        7: "sales_territory",
        8: "month",
        9: "day",
        10: "sales_territory",
        14: "production_line",
        15: "factory",
        17: "product",
        18: "product_category",
        21: "product",
        22: "scrap_reason",
        23: "month",
        26: "product",
        27: "product_category",
        32: "product",
    }
    period = item["period"]
    if number in {35, 36, 37, 38}:
        period = "this_month" if number in {37, 38} else "last_month"
    if number == 40:
        period = None
    if number == 34:
        period = "recently"
    if number == 39:
        period = "last_month"
    params = item.get("parameters") or {}
    return Intent(
        metric_id=item["metric_ids"][0] if item["metric_ids"] else None,
        dimension=dims.get(number, "none"),
        period=period,
        start_date=params.get("start"),
        end_date=params.get("end"),
        factory_id=(
            2 if number == 36 else (1 if number == 39 else params.get("factory_id"))
        ),
        territory=params.get("territory"),
        limit=3 if number == 10 else (10 if number in {17, 26} else 100),
        needs_clarification=number in {33, 34, 39},
        clarification_question=(
            "Please specify the metric or period." if number in {33, 34, 39} else None
        ),
        zero_scrap_only=number == 32,
    )


def reference_parameters(item: dict[str, Any], anchor: date) -> dict[str, Any]:
    params = dict(item.get("parameters") or {})
    if item["period"] and item["period"] != "explicit":
        start, end = resolve_period(item["period"], anchor)
        params.update(start=start, end=end)
        if "sales_growth" in item["metric_ids"]:
            params.update(
                baseline_start=month_start(
                    start, -3 if item["period"] == "last_quarter" else -1
                ),
                baseline_end=start,
            )
    return params


def close(key: str, expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, (date, datetime)):
        return str(expected) == str(actual)
    if isinstance(expected, (int, float, Decimal)):
        tolerance = (
            Decimal("0.000001")
            if key in {"defect_rate", "sales_growth"}
            else Decimal("0.01")
        )
        try:
            return abs(Decimal(str(expected)) - Decimal(str(actual))) <= tolerance
        except Exception:
            return False
    return str(expected) == str(actual)


def main() -> None:
    credentials = dict(
        line.split(": ", 1)
        for line in sys.stdin.read().splitlines()[1:]
        if ": " in line
    )
    questions = yaml.safe_load(
        (ROOT / "data/eval/golden_questions.yaml").read_text(encoding="utf-8")
    )["questions"]
    answers = {q["question"]: intent_for(q) for q in questions}
    answers["And by territory?"] = Intent(
        metric_id=None,
        dimension="sales_territory",
        period=None,
        start_date=None,
        end_date=None,
        factory_id=None,
        territory=None,
        limit=100,
        needs_clarification=False,
        clarification_question=None,
        zero_scrap_only=False,
    )
    fake = FakeLLM(answers)
    outcomes: list[dict[str, Any]] = []
    with TestClient(app) as client:
        app.state.llm = fake
        anchor = date.fromisoformat(app.state.readiness["data_as_of"])
        tokens = {}
        for username in credentials:
            result = client.post(
                "/api/auth/login",
                json={"username": username, "password": credentials[username]},
            )
            assert result.status_code == 200, username
            tokens[username] = result.json()["access_token"]
        for item in questions:
            ident = item["id"]
            response = client.post(
                "/api/chat/ask",
                json={"question": item["question"]},
                headers={"Authorization": f"Bearer {tokens[item['user']]}"},
            )
            body = response.json()
            assert body["status"] == item["expected_status"], (
                ident,
                response.status_code,
                body["status"],
                body["message"],
            )
            assert response.status_code == (
                403 if body["status"] == "denied" else 200
            ), ident
            if body["status"] in {"ok", "no_data"}:
                params = reference_parameters(item, anchor)
                with app.state.warehouse.connect() as connection, connection.begin():
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    reference = [
                        dict(row)
                        for row in connection.execute(
                            text(item["reference_sql"]), params
                        ).mappings()
                    ]
                assert len(body["table"]) == len(reference), (
                    ident,
                    "row_count",
                    len(body["table"]),
                    len(reference),
                )
                for index, (actual, expected) in enumerate(
                    zip(body["table"], reference)
                ):
                    assert item["metric_ids"][0] in actual, (ident, "metric_missing")
                    for key, value in expected.items():
                        assert key in actual, (ident, "missing_column", key)
                        if key in actual:
                            assert close(key, value, actual[key]), (
                                ident,
                                index,
                                key,
                                str(value),
                                str(actual[key]),
                            )
                assert body["sources"]["data_as_of"] == anchor.isoformat(), ident
            outcomes.append(
                {"id": ident, "status": body["status"], "row_count": len(body["table"])}
            )
        # Phase 3 also proposes a chart for each nonempty result.
        # Shortcut answers (see special_kind) make no LLM calls.
        shortcuts = sum(special_kind(q["question"]) is not None for q in questions)
        # Unambiguous questions are interpreted locally, with no model call.
        local = sum(
            q["user"] != "it_admin"  # rejected before interpretation
            and special_kind(q["question"]) is None
            and local_intent(q["question"]) is not None
            for q in questions
        )
        assert fake.calls == len(questions) - 1 - shortcuts - local + sum(
            q["expected_status"] == "ok" for q in questions
        )
        # A follow-up inherits the same user's metric and period slots.
        owner = client.post(
            "/api/chat/ask",
            json={"question": questions[0]["question"]},
            headers={"Authorization": f"Bearer {tokens['sales']}"},
        ).json()["conversation_id"]
        followup = client.post(
            "/api/chat/ask",
            json={"question": "And by territory?", "conversation_id": owner},
            headers={"Authorization": f"Bearer {tokens['sales']}"},
        )
        assert followup.status_code == 200 and followup.json()["status"] == "ok"
        assert followup.json()["conversation_id"] == owner
        assert all("territory" in row for row in followup.json()["table"])
        # Conversation identifiers belong to one user; a different role cannot reuse them.
        foreign = client.post(
            "/api/chat/ask",
            json={"question": questions[0]["question"], "conversation_id": owner},
            headers={"Authorization": f"Bearer {tokens['manager']}"},
        )
        assert foreign.status_code == 404
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": "FakeLLM",
        "questions": len(outcomes),
        "passed": len(outcomes),
        "outcomes": outcomes,
        "live_groq_tested": False,
        "conversation_isolation": "passed",
        "followup_slots": "passed",
    }
    path = Path(os.getenv("ACBI_PHASE2_REPORT", "/tmp/acbi-phase2.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Phase 2: {len(outcomes)} golden cases passed with FakeLLM; conversation isolation passed"
    )


if __name__ == "__main__":
    main()
