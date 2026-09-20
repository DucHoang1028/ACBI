"""Conversation regressions with FakeLLM and independently queried real data."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.ai.client import FakeLLM, Intent  # noqa: E402
from app.main import app  # noqa: E402


def main() -> None:
    credentials = dict(
        line.split(": ", 1) for line in sys.stdin.read().splitlines() if ": " in line
    )
    base = dict(
        metric_id="revenue",
        dimension="none",
        period=None,
        start_date=None,
        end_date=None,
        factory_id=None,
        territory=None,
        limit=100,
        needs_clarification=True,
        clarification_question="Which period?",
        zero_scrap_only=False,
        missing_fields=["period"],
    )
    questions = [
        "Doanh thu",
        "Tháng 6 năm 2025",
        "Còn quý trước?",
        "Doanh thu tháng này",
    ]
    answers = {q: Intent.model_validate(base) for q in questions}
    answers[questions[1]] = Intent.model_validate(
        {**base, "metric_id": None, "missing_fields": ["metric_id"]}
    )
    answers[questions[2]] = Intent.model_validate(
        {
            **base,
            "metric_id": None,
            "period": "last_year",
            "missing_fields": ["metric_id", "period"],
        }
    )
    with TestClient(app) as client:
        app.state.llm = FakeLLM(answers)
        token = client.post(
            "/api/auth/login",
            json={"username": "manager", "password": credentials["manager"]},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        conversation = None
        for index, question in enumerate(questions):
            result = client.post(
                "/api/chat/ask",
                headers=headers,
                json={
                    "question": question,
                    "conversation_id": conversation,
                    "language": "vi",
                },
            )
            result.raise_for_status()
            payload = result.json()
            conversation = payload["conversation_id"]
            if index == 0:
                assert payload["status"] == "needs_clarification"
                continue
            assert payload["status"] == "ok", payload["status"]
            assert payload["saved"]
            start, end = [
                (date(2025, 6, 1), date(2025, 7, 1)),
                (date(2025, 1, 1), date(2025, 4, 1)),
                (date(2025, 6, 1), date(2025, 6, 30)),
            ][index - 1]
            assert payload["sources"]["parameters"]["start"] == str(start)
            assert payload["sources"]["parameters"]["end"] == str(end)
            with app.state.warehouse.connect() as connection:
                expected = connection.execute(
                    text(
                        "SELECT SUM(subtotal) FROM sales.salesorderheader "
                        "WHERE orderdate>=:start AND orderdate<:end"
                    ),
                    {"start": start, "end": end},
                ).scalar_one()
            assert abs(
                Decimal(str(payload["table"][0]["revenue"])) - expected
            ) < Decimal("0.01")
        client.post("/api/auth/logout")
    print(
        "Conversation: clarification -> explicit month -> last quarter -> this month "
        "passed; all revenue values reconciled with reference SQL. No LLM calls."
    )


if __name__ == "__main__":
    main()
