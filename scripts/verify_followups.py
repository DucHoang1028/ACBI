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

STACKED = "Doanh thu theo tháng và khu vực dạng cột chồng năm 2023"
KPI = "Doanh thu năm 2023 dạng thẻ KPI"
PIE = "Doanh thu theo danh mục sản phẩm năm 2023 dạng biểu đồ tròn"
LINE = "Doanh thu theo tháng năm 2023 dạng biểu đồ đường"
OUTPUT = "Sản lượng theo tháng và dây chuyền dạng cột chồng năm {year}"
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

        def variant(**changes: object) -> Intent:
            return Intent.model_validate({**clarify.model_dump(), **changes})

        with app.state.warehouse.connect() as connection:
            year = int(
                connection.execute(
                    text(
                        "SELECT EXTRACT(YEAR FROM MAX(enddate)) FROM production.workorder"
                    )
                ).scalar_one()
            )
        output = OUTPUT.format(year=year)
        app.state.llm = FakeLLM(
            {
                SHARE: clarify,
                STACKED: clarify,
                KPI: clarify,
                output: variant(metric_id="production_output"),
                PIE: variant(dimension="product_category"),
                LINE: variant(dimension="month"),
            }
        )
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

        # Every chart type in the proposal, on real data, checked against SQL.
        def reference_total(sql: str, **params: object) -> Decimal:
            with app.state.warehouse.connect() as connection:
                return Decimal(str(connection.execute(text(sql), params).scalar_one()))

        bounds = {"s": date(2023, 1, 1), "e": date(2024, 1, 1)}
        revenue_2023 = reference_total(
            "SELECT SUM(subtotal) FROM sales.salesorderheader "
            "WHERE orderdate>=:s AND orderdate<:e",
            **bounds,
        )
        stacked = ask("manager", STACKED)
        assert stacked["status"] == "ok", stacked
        viz = stacked["viz_config"]
        assert viz["type"] == "stacked_bar" and viz["series"] == "territory", viz
        assert abs(
            sum(Decimal(str(r["revenue"])) for r in stacked["table"]) - revenue_2023
        ) < Decimal("0.05"), "stacked rows must sum to the reference total"
        stacked_production = ask("manager", output)
        assert stacked_production["status"] == "ok", stacked_production
        assert (
            stacked_production["viz_config"]["type"] == "stacked_bar"
        ), stacked_production["viz_config"]
        kpi = ask("manager", KPI)
        assert kpi["viz_config"]["type"] == "kpi_card" and len(kpi["table"]) == 1, kpi
        assert abs(Decimal(str(kpi["table"][0]["revenue"])) - revenue_2023) < Decimal(
            "0.01"
        )
        pie_direct = ask("manager", PIE)
        assert pie_direct["viz_config"]["type"] == "pie", pie_direct
        line = ask("manager", LINE)
        assert line["viz_config"]["type"] == "line", line
        for wording, kind in (
            ("Đổi sang biểu đồ phân tán", "scatter"),
            ("Đổi sang biểu đồ cột", "bar"),
            ("Đổi sang biểu đồ vành khuyên", "bar"),  # 10 groups: too many
        ):
            reshaped = ask("manager", wording, share["conversation_id"])
            assert reshaped["viz_config"]["type"] == kind, (wording, reshaped)
        for wording, kind in (
            ("Đổi sang biểu đồ tròn", "pie"),
            ("Đổi sang biểu đồ vành khuyên", "donut"),
            ("Đổi sang biểu đồ đường", "bar"),  # categories are not ordered
        ):
            reshaped = ask("manager", wording, pie_direct["conversation_id"])
            assert reshaped["viz_config"]["type"] == kind, (wording, reshaped)

        # Reopening a conversation restores every turn, in order, for its owner only.
        reopened = client.get(
            f"/api/conversations/{conversation}", headers=tokens["manager"]
        )
        assert reopened.status_code == 200, reopened.text
        asked = [t["question"] for t in reopened.json()["transcript"]]
        assert asked == [
            SHARE,
            "Đổi sang biểu đồ tròn",
            "Hiển thị dạng bảng",
            "Đổi sang biểu đồ phân tán",
            "Đổi sang biểu đồ cột",
            "Đổi sang biểu đồ vành khuyên",
        ], asked
        assert all(t["answer"]["saved"] for t in reopened.json()["transcript"])
        foreign = client.get(
            f"/api/conversations/{conversation}", headers=tokens["production_a"]
        )
        assert foreign.status_code == 404, foreign.status_code
        client.post("/api/auth/logout")
    print(
        "Follow-ups: territory list (scoped), Germany+UK share, stacked bar, KPI "
        "card, pie, line, scatter and bar reconciled with reference SQL; reopened conversation restores every turn."
    )


if __name__ == "__main__":
    main()
