# ruff: noqa: E501
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
EFFICIENCY = "Hiệu suất các dây chuyền sản xuất hôm nay như thế nào?"
STAFF = "Doanh thu theo nhân viên bán hàng năm 2024"
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
        anchor_day = app.state.readiness["data_as_of"]
        app.state.llm = FakeLLM(
            {
                SHARE: clarify,
                STACKED: clarify,
                KPI: clarify,
                output: variant(metric_id="production_output"),
                PIE: variant(dimension="product_category"),
                LINE: variant(dimension="month"),
                EFFICIENCY: variant(metric_id="production_output", period="today"),
                STAFF: variant(period="explicit", needs_clarification=False),
            }
        )
        tokens = {}
        for user in ("manager", "production_a", "sales"):
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

        # Conversational answers are grounded in the rows and the dictionary.
        two = ask("manager", "cho tôi doanh thu của Đức và Pháp năm 2025 đi")
        assert two["status"] == "ok" and len(two["table"]) == 2, two
        which = ask(
            "manager", "giữa 2 cái thì cái nào nhiều hơn", two["conversation_id"]
        )
        assert which["status"] == "ok" and which["saved"], which
        by_name = {r["territory"]: Decimal(str(r["revenue"])) for r in two["table"]}
        winner = max(by_name, key=lambda k: by_name[k])
        assert which["answer_text"].startswith(f"{winner} cao hơn"), which[
            "answer_text"
        ]
        assert which["table"] == two["table"] and which["viz_config"]["type"] == "table"
        meaning = ask(
            "manager",
            "germany trong dữ liệu bạn nói tiếng việt là gì",
            two["conversation_id"],
        )
        assert "Germany là Đức" in meaning["answer_text"], meaning
        hello = ask("manager", "Chào bạn", two["conversation_id"])
        assert hello["status"] == "ok" and "Xin chào" in hello["answer_text"], hello

        # Forecast: computed from recorded months, labelled, or refused with a reason.
        forecast = ask(
            "manager",
            "Dựa trên doanh thu của đức và pháp thì bạn có dự đoán được trong năm 2026 "
            "doanh thu sẽ phát triển theo hướng nào",
        )
        assert forecast["status"] in {"ok", "needs_clarification"}, forecast
        if forecast["status"] == "ok":
            info = forecast["sources"]["forecast"]
            assert info["is_forecast"] and info["history_months"] >= 24, info
            assert forecast["viz_config"]["type"] == "line"
            assert any(r["forecast"] for r in forecast["table"]) and any(
                r["actual"] for r in forecast["table"]
            )
            assert forecast["answer_text"].startswith(
                "Dự báo, không phải số liệu"
            ), forecast["answer_text"]
            last = [r for r in forecast["table"] if r["actual"]][-1]
            with app.state.warehouse.connect() as connection:
                recorded = connection.execute(
                    text(
                        "SELECT SUM(h.subtotal) FROM sales.salesorderheader h "
                        "JOIN sales.salesterritory t USING(territoryid) "
                        "WHERE t.name IN ('France','Germany') "
                        "AND date_trunc('month',h.orderdate)=CAST(:m AS date)"
                    ),
                    {"m": last["month"]},
                ).scalar_one()
            assert abs(Decimal(last["actual"]) - Decimal(str(recorded))) < Decimal(
                "0.01"
            )
        else:
            assert "không đưa ra dự báo" in forecast["message"], forecast
        print(
            "forecast ->",
            forecast["status"],
            (forecast.get("answer_text") or forecast["message"])[:230],
        )
        outside = ask("sales", "Dự báo sản lượng tháng tới")
        assert outside["status"] == "denied" and outside["http"] == 403, outside
        rate = ask("manager", "Dự báo tỷ lệ phế phẩm năm tới")
        assert rate["status"] == "needs_clarification", rate
        materials = ask(
            "manager", "Vật tư nào có nguy cơ thiếu cho kế hoạch sản xuất tuần tới?"
        )
        assert materials["status"] == "needs_clarification"
        assert "billofmaterials" in materials["message"], materials

        # New approved metric, day/week periods, and questions that must ask first.
        ontime = ask("manager", "Tỷ lệ hoàn thành đúng hạn năm 2024")
        assert ontime["status"] == "ok", ontime
        with app.state.warehouse.connect() as connection:
            expected_ratio = connection.execute(
                text(
                    "SELECT COUNT(*) FILTER (WHERE enddate<=duedate)::numeric/COUNT(*) "
                    "FROM production.workorder "
                    "WHERE enddate>=DATE '2024-01-01' AND enddate<DATE '2025-01-01'"
                )
            ).scalar_one()
        assert abs(
            Decimal(str(ontime["table"][0]["on_time_rate"])) - expected_ratio
        ) < (Decimal("0.000001"))
        by_line = ask("manager", "Tỷ lệ hoàn thành đúng hạn theo dây chuyền năm 2024")
        assert by_line["status"] == "ok" and by_line["table"], by_line
        assert all(0 <= float(r["on_time_rate"]) <= 1 for r in by_line["table"])
        today = ask("manager", "Sản lượng hôm nay")
        assert today["status"] in {"ok", "no_data"}, today
        assert today["sources"]["parameters"]["start"] == anchor_day, today["sources"]
        efficiency = ask("manager", EFFICIENCY)
        assert efficiency["status"] == "needs_clarification", efficiency
        assert "đúng hạn" in efficiency["message"], efficiency
        staff = ask("manager", STAFF)
        assert staff["status"] == "needs_clarification", staff
        assert "nhân viên bán hàng" in staff["message"], staff
        client.post("/api/auth/logout")
    print(
        "Follow-ups: territory list (scoped), Germany+UK share, stacked bar, KPI "
        "card, pie, line, scatter and bar reconciled with reference SQL; reopened conversation restores every turn."
    )


if __name__ == "__main__":
    main()
