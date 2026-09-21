# ruff: noqa: E501
"""Live-model evaluation: golden and edge-case questions through the real Groq path.

Usage: python scripts/eval_groq.py [--suite golden|edge|all] [--local-intent]
Credentials are read from stdin (deploy/seed-credentials.txt). By default the
deterministic shortcut is disabled so every question really reaches the model.
"""

import json
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.dates import resolve_period  # noqa: E402
from app.main import app  # noqa: E402
from verify_phase2 import close, reference_parameters  # noqa: E402

USERS = {
    "manager": "manager",
    "sales": "sales",
    "production_a": "production_a",
    "it_admin": "it_admin",
}
TOTALS = {
    "revenue": (
        "SELECT SUM(subtotal) AS revenue FROM sales.salesorderheader "
        "WHERE orderdate>=:s AND orderdate<:e"
    ),
    "production_output": (
        "SELECT SUM(orderqty-scrappedqty) AS production_output FROM production.workorder "
        "WHERE enddate>=:s AND enddate<:e"
    ),
    "defect_rate": (
        "SELECT SUM(scrappedqty)::numeric/NULLIF(SUM(orderqty),0) AS defect_rate "
        "FROM production.workorder WHERE enddate>=:s AND enddate<:e"
    ),
}


def ask(
    client: TestClient, headers: dict[str, str], question: str, conversation: str | None
) -> tuple[dict[str, Any], int]:
    """Ask once; wait and retry while the local or provider rate limits are busy."""
    retries = 0
    while True:
        result = client.post(
            "/api/chat/ask",
            headers=headers,
            json={
                "question": question,
                "conversation_id": conversation,
                "language": "vi",
            },
        )
        body = result.json()
        if body.get("status") == "technical_failure" and retries < 8:
            retries += 1
            time.sleep(15)
            continue
        return {"http": result.status_code, **body}, retries


def window(spec: Any, anchor: date) -> tuple[str, str]:
    if isinstance(spec, str):
        start, end = resolve_period(spec, anchor)
        return start.isoformat(), end.isoformat()
    return str(spec[0]), str(spec[1])


def reference_total(metric: str, start: str, end: str) -> Decimal | None:
    with app.state.warehouse.connect() as connection:
        value = connection.execute(
            text(TOTALS[metric]), {"s": start, "e": end}
        ).scalar_one_or_none()
    return None if value is None else Decimal(str(value))


def check_edge(item: dict[str, Any], body: dict[str, Any], anchor: date) -> list[str]:
    problems: list[str] = []
    if body["status"] not in item["expect"]:
        problems.append(f"status {body['status']} not in {item['expect']}")
        return problems
    said = f"{body.get('answer_text') or ''} {body.get('message') or ''}"
    if item.get("says") and item["says"].lower() not in said.lower():
        problems.append(f"answer lacks {item['says']!r}: {said[:200]}")
    if item.get("avoid") and item["avoid"].lower() in said.lower():
        problems.append(f"answer must not mention {item['avoid']!r}: {said[:200]}")
    if body["status"] != "ok":
        if body["status"] == "denied" and body.get("table"):
            problems.append("denied response carried data")
        return problems
    said = f"{body.get('answer_text') or ''} {body.get('message') or ''}"
    if item.get("says") and item["says"].lower() not in said.lower():
        problems.append(f"answer lacks {item['says']!r}: {said[:200]}")
    if item.get("avoid") and item["avoid"].lower() in said.lower():
        problems.append(f"answer must not mention {item['avoid']!r}: {said[:200]}")
    if "Traceback" in said or "Specify both" in said:
        problems.append(f"internal text leaked: {said[:200]}")
    sources = body.get("sources") or {}
    if item.get("metric") and item["metric"] not in (
        sources.get("metric_versions") or {}
    ):
        problems.append(
            f"metric {item['metric']} not in {sources.get('metric_versions')}"
        )
    if item.get("window") and sources.get("parameters"):
        expected = window(item["window"], anchor)
        got = (
            str(sources["parameters"].get("start")),
            str(sources["parameters"].get("end")),
        )
        if got != expected:
            problems.append(f"window {got} != {expected}")
    if item.get("rows_max") and len(body["table"]) > item["rows_max"]:
        problems.append(f"{len(body['table'])} rows > {item['rows_max']}")
    if (
        item.get("chart")
        and (body.get("viz_config") or {}).get("type") != item["chart"]
    ):
        problems.append(
            f"chart {(body.get('viz_config') or {}).get('type')} != {item['chart']}"
        )
    if item.get("verify") and item.get("metric") and len(body["table"]) == 1:
        start, end = window(item["window"], anchor)
        expected_value = reference_total(item["metric"], start, end)
        actual = body["table"][0].get(item["metric"])
        if expected_value is None or not close(item["metric"], expected_value, actual):
            problems.append(f"value {actual} != reference {expected_value}")
    if body.get("table") and not body.get("saved"):
        problems.append("result not saved")
    return problems


def check_golden(item: dict[str, Any], body: dict[str, Any], anchor: date) -> list[str]:
    if body["status"] != item["expected_status"]:
        return [
            f"status {body['status']} != {item['expected_status']}: {body.get('message')}"
        ]
    if body["status"] not in {"ok", "no_data"}:
        return []
    params = reference_parameters(item, anchor)
    with app.state.warehouse.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        reference = [
            dict(r)
            for r in connection.execute(text(item["reference_sql"]), params).mappings()
        ]
    if len(body["table"]) != len(reference):
        return [f"rows {len(body['table'])} != reference {len(reference)}"]
    problems: list[str] = []
    for index, (actual, expected) in enumerate(zip(body["table"], reference)):
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"row {index}: missing column {key}")
            elif not close(key, value, actual[key]):
                problems.append(f"row {index}: {key} {actual[key]} != {value}")
    return problems[:3]


def main() -> None:
    suite = sys.argv[sys.argv.index("--suite") + 1] if "--suite" in sys.argv else "all"
    credentials = dict(
        line.split(": ", 1)
        for line in sys.stdin.read().splitlines()[1:]
        if ": " in line
    )
    golden = yaml.safe_load(
        (ROOT / "data/eval/golden_questions.yaml").read_text(encoding="utf-8")
    )["questions"]
    edge = yaml.safe_load(
        (ROOT / "data/eval/groq_edge_cases.yaml").read_text(encoding="utf-8")
    )
    report: list[dict[str, Any]] = []
    with TestClient(app) as client:
        assert (
            app.state.llm is not None and type(app.state.llm).__name__ == "GroqClient"
        )
        app.state.settings.local_intent_enabled = "--local-intent" in sys.argv
        anchor = date.fromisoformat(app.state.readiness["data_as_of"])
        tokens: dict[str, dict[str, str]] = {}
        for user in USERS:
            login = client.post(
                "/api/auth/login",
                json={"username": user, "password": credentials[user]},
            )
            assert login.status_code == 200, user
            tokens[user] = {"Authorization": f"Bearer {login.json()['access_token']}"}
        conversations: dict[str, str] = {}
        only = (
            set(sys.argv[sys.argv.index("--ids") + 1].split(","))
            if "--ids" in sys.argv
            else None
        )
        plan = (
            [("golden", g) for g in golden] if suite in {"golden", "all"} else []
        ) + ([("edge", e) for e in edge] if suite in {"edge", "all"} else [])
        for kind, item in plan:
            if only and item["id"] not in only:
                continue
            group = item.get("conversation")
            body, retries = ask(
                client,
                tokens[item["user"]],
                item["question"],
                conversations.get(group) if group else None,
            )
            if group and body.get("conversation_id"):
                conversations[group] = body["conversation_id"]
            problems = (check_golden if kind == "golden" else check_edge)(
                item, body, anchor
            )
            report.append(
                {
                    "suite": kind,
                    "id": item["id"],
                    "user": item["user"],
                    "question": item["question"],
                    "status": body["status"],
                    "message": (body.get("message") or "")[:120],
                    "viz": (body.get("viz_config") or {}).get("type"),
                    "rows": len(body.get("table") or []),
                    "rate_retries": retries,
                    "problems": problems,
                    "passed": not problems,
                }
            )
            mark = "PASS" if not problems else "FAIL"
            print(
                f"{mark} {item['id']:>5} [{body['status']}] {item['question'][:70]}",
                flush=True,
            )
            for problem in problems:
                print(f"       -> {problem}", flush=True)
        client.post("/api/auth/logout")
    out = Path("/tmp/groq_results.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("golden", "edge"):
        part = [r for r in report if r["suite"] == name]
        if part:
            print(f"{name}: {sum(r['passed'] for r in part)}/{len(part)} passed")


if __name__ == "__main__":
    main()
