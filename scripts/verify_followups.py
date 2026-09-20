"""Listing, share-of-total and redisplay questions against real data (FakeLLM)."""

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

SHARE = "Nếu tính riêng doanh thu Đức và Anh thì nó chiếm bao nhiêu phần trăm năm 2023"


def main() -> None:
    credentials = dict(
        line.split(": ", 1) for line in sys.stdin.read().splitlines() if ": " in line
    )
    clarify = Intent.model_validate(
        {
            "metric_id": "revenue",
            "dimension": "none",
            "period": None,
            "start_date": None,
            "end_date": None,
            "factory_id": None,
            "territory": None,
            "limit": 100,
            "needs_clarification": True,
            "clarification_question": "Which period?",
            "zero_scrap_only": False,
            "missing_fields": ["period"],
        }
    )
    with TestClient(app) as client:
        app.state.llm = FakeLLM({SHARE: clarify})
        tokens = {}
        for user in ("manager", "production_a"):
            login = client.post(
                "/api/auth/login",
                json={"username": user, "password": credentials[user]},
            )
            assert login.status_code == 200, user
            tokens[user] = {"Authorization": f"Bearer {login.json()['access_token']}"}

        def ask(user: str, question: str, conversation: str | None = None) -> dict:
            result = client.post(
                "/api/chat/ask",
                headers=tokens[user],
                json={
                    "question": question,
                    "conversation_id": conversation,
                    "language": "vi",
                },
            )
            return {"http": result.status_code, **result.json()}

        listing = ask(
            "manager", "Cho tôi tất cả khu vực hiện tại đang có trong dữ liệu"
        )
        assert listing["status"] == "ok" and len(listing["table"]) == 10, listing
        denied = ask("production_a", "Cho tôi tất cả khu vực hiện tại đang có")
        assert denied["status"] == "denied" and denied["http"] == 403, denied

        share = ask("manager", SHARE)
        assert share["status"] == "ok", share
        start, end = date(2023, 1, 1), date(2024, 1, 1)
        with app.state.warehouse.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT t.name,SUM(h.subtotal) FROM sales.salesorderheader h "
                    "JOIN sales.salesterritory t USING(territoryid) "
                    "WHERE h.orderdate>=:s AND h.orderdate<:e GROUP BY t.name"
                ),
                {"s": start, "e": end},
            ).all()
        total = sum(Decimal(r[1]) for r in rows)
        picked = sum(
            Decimal(r[1]) for r in rows if r[0] in {"Germany", "United Kingdom"}
        )
        expected = f"{(picked / total * 100):.2f}%"
        assert len(share["table"]) == 10 and expected in share["answer_text"], (
            expected,
            share["answer_text"],
        )

        conversation = share["conversation_id"]
        pie = ask("manager", "Đổi sang biểu đồ tròn", conversation)
        assert pie["status"] == "ok" and pie["viz_config"]["type"] == "bar", pie
        assert pie["table"] == share["table"] and pie["saved"], pie
        table = ask("manager", "Hiển thị dạng bảng", conversation)
        assert table["viz_config"]["type"] == "table" and table["saved"], table
        lonely = ask("manager", "Đổi sang biểu đồ tròn")
        assert lonely["status"] == "needs_clarification", lonely
        client.post("/api/auth/logout")
    print(
        "Follow-ups: territory list (scoped), Germany+UK share reconciled with "
        "reference SQL, redisplay as bar/table, no LLM calls."
    )


if __name__ == "__main__":
    main()
