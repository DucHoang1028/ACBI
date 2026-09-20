"""Exercise both Phase 3 paths with FakeLLM and the live read-only warehouse."""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.client import FakeLLM, Intent, SQLCandidate  # noqa: E402
from app.core.dates import month_start, resolve_period  # noqa: E402
from app.main import app  # noqa: E402
from app.presentation.charts import VizConfig  # noqa: E402
from verify_phase2 import close  # noqa: E402


def intent_for(item: dict[str, Any]) -> Intent:
    return Intent(
        metric_id=item["metric_id"],
        dimension=item["dimension"],
        period=item["period"],
        start_date=item.get("start_date"),
        end_date=item.get("end_date"),
        factory_id=item.get("factory_id"),
        territory=None,
        limit=100,
        needs_clarification=False,
        clarification_question=None,
        zero_scrap_only=False,
    )


def main() -> None:
    credentials = dict(
        line.split(": ", 1)
        for line in sys.stdin.read().splitlines()[1:]
        if ": " in line
    )
    cases = yaml.safe_load(
        (ROOT / "data/eval/phase3_questions.yaml").read_text(encoding="utf-8")
    )["questions"]
    examples = {
        e["id"]: e
        for e in yaml.safe_load(
            (ROOT / "data/business_dictionary/sql_examples.yaml").read_text(
                encoding="utf-8"
            )
        )["examples"]
    }
    intents = {item["question"]: intent_for(item) for item in cases}
    candidates = {
        item["question"]: [
            SQLCandidate(sql=examples[item["example"]]["sql"], missing_information=None)
        ]
        for item in cases
    }
    visuals = {
        item["question"]: [
            VizConfig(
                type=item["chart_type"],
                x=(
                    item["dimension"]
                    if item["dimension"] != "sales_territory"
                    else "territory"
                ),
                y=[item["metric_id"]],
                series=None,
            )
        ]
        for item in cases
        if item["expected_status"] == "ok"
    }
    fake = FakeLLM(intents, candidates, visuals)
    results = []
    with TestClient(app) as client:
        app.state.llm = fake
        anchor = date.fromisoformat(app.state.readiness["data_as_of"])
        tokens = {}
        for name in credentials:
            login = client.post(
                "/api/auth/login",
                json={"username": name, "password": credentials[name]},
            )
            assert login.status_code == 200, name
            tokens[name] = login.json()["access_token"]
        warehouse_statements: list[str] = []

        def record(
            _conn: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            warehouse_statements.append(statement)

        event.listen(app.state.warehouse, "before_cursor_execute", record)
        try:
            for item in cases:
                question = item["question"]
                request = client.post(
                    "/api/chat/ask",
                    json={"question": question},
                    headers={"Authorization": f"Bearer {tokens[item['user']]}"},
                )
                body = request.json()
                assert (
                    request.status_code == 200
                    and body["status"] == item["expected_status"]
                ), (item["id"], request.status_code, body)
                assert body["query_path"] == "rag_text_to_sql", item["id"]
                assert body["sources"]["data_as_of"] == anchor.isoformat()
                assert body["sources"]["references"]
                assert body["llm_calls"] <= 3
                if item["expected_status"] == "ok":
                    assert body["viz_config"]["type"] == item["chart_type"], item["id"]
                else:
                    assert not body["table"]
                if item["period"] == "explicit":
                    parameters = {
                        "start": date.fromisoformat(item["start_date"]),
                        "end": date.fromisoformat(item["end_date"]),
                    }
                else:
                    start, end = resolve_period(item["period"], anchor)
                    parameters = {"start": start, "end": end}
                    if item["metric_id"] == "sales_growth":
                        parameters.update(
                            baseline_start=month_start(start, -1), baseline_end=start
                        )
                with app.state.warehouse.connect() as connection, connection.begin():
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    expected = [
                        dict(row)
                        for row in connection.execute(
                            text(item["reference_sql"]), parameters
                        ).mappings()
                    ]
                assert len(body["table"]) == len(expected), (
                    item["id"],
                    len(body["table"]),
                    len(expected),
                )
                for actual, reference in zip(body["table"], expected):
                    assert set(reference) <= set(actual), (
                        item["id"],
                        set(reference) - set(actual),
                    )
                    for key, value in reference.items():
                        assert close(key, value, actual[key]), (
                            item["id"],
                            key,
                            str(value),
                            str(actual[key]),
                        )
                results.append(
                    {
                        "id": item["id"],
                        "status": body["status"],
                        "rows": len(body["table"]),
                        "llm_calls": body["llm_calls"],
                    }
                )
            corrected_question = "Revenue by territory with chart correction"
            fake.answers[corrected_question] = Intent(
                metric_id="revenue",
                dimension="sales_territory",
                period="last_month",
                start_date=None,
                end_date=None,
                factory_id=None,
                territory=None,
                limit=100,
                needs_clarification=False,
                clarification_question=None,
                zero_scrap_only=False,
            )
            fake.viz_answers[corrected_question] = [
                VizConfig(type="line", x="territory", y=["revenue"], series=None),
                VizConfig(type="bar", x="territory", y=["revenue"], series=None),
            ]
            corrected = client.post(
                "/api/chat/ask",
                json={"question": corrected_question},
                headers={"Authorization": f"Bearer {tokens['sales']}"},
            )
            assert corrected.status_code == 200
            assert corrected.json()["viz_config"]["type"] == "bar"
            assert corrected.json()["llm_calls"] == 3
            fallback_question = "Growth by territory with invalid chart"
            fake.answers[fallback_question] = intents[cases[4]["question"]]
            fake.sql_answers[fallback_question] = [
                SQLCandidate(
                    sql=examples[cases[4]["example"]]["sql"], missing_information=None
                )
            ]
            fake.viz_answers[fallback_question] = [
                VizConfig(type="line", x="territory", y=["sales_growth"], series=None)
            ]
            fallback = client.post(
                "/api/chat/ask",
                json={"question": fallback_question},
                headers={"Authorization": f"Bearer {tokens['manager']}"},
            )
            assert fallback.status_code == 200
            assert fallback.json()["status"] == "ok" and fallback.json()["table"]
            assert fallback.json()["viz_config"]["type"] == "table"
            assert fallback.json()["chart_fallback"] is True
            # Denied requests run no warehouse statements.
            question = "Denied generated scrap query"
            fake.answers[question] = Intent(
                metric_id="defect_rate",
                dimension="day",
                period="last_month",
                start_date=None,
                end_date=None,
                factory_id=None,
                territory=None,
                limit=100,
                needs_clarification=False,
                clarification_question=None,
                zero_scrap_only=False,
            )
            fake.sql_answers[question] = [
                SQLCandidate(
                    sql=examples["example:defect_rate:daily"]["sql"],
                    missing_information=None,
                )
            ]
            before = len(warehouse_statements)
            denied = client.post(
                "/api/chat/ask",
                json={"question": question},
                headers={"Authorization": f"Bearer {tokens['sales']}"},
            )
            assert denied.status_code == 403 and denied.json()["status"] == "denied"
            assert len(warehouse_statements) == before
            # An allowed intent cannot change its approved formula.
            question = "Malicious formula proposal"
            fake.answers[question] = intents[cases[1]["question"]]
            fake.sql_answers[question] = [
                SQLCandidate(
                    sql=(
                        "SELECT SUM(w.scrappedqty) AS production_output "
                        "FROM production.workorder w GROUP BY w.enddate"
                    ),
                    missing_information=None,
                )
            ]
            before = len(warehouse_statements)
            denied = client.post(
                "/api/chat/ask",
                json={"question": question},
                headers={"Authorization": f"Bearer {tokens['manager']}"},
            )
            assert denied.status_code == 403 and denied.json()["status"] == "denied"
            assert len(warehouse_statements) == before
        finally:
            event.remove(app.state.warehouse, "before_cursor_execute", record)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": "FakeLLM",
        "rag_cases": len(results),
        "passed": len(results),
        "results": results,
        "denied_without_warehouse_sql": True,
        "generated_formula_change_denied": True,
        "invalid_chart_corrected": True,
        "invalid_chart_fallback": True,
        "live_rag_metadata_tested": False,
    }
    path = Path(os.getenv("ACBI_PHASE3_REPORT", "/tmp/acbi-phase3.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Phase 3: {len(results)} RAG cases reconciled; safety checks passed")


if __name__ == "__main__":
    main()
