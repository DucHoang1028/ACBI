from datetime import date

import pytest
from app.ai.client import Intent
from app.conversation.intent import merged_intent
from app.core.dates import date_hints, resolve_period
from app.presentation.summary import factual
from app.query.builder import build
from app.query.shortcuts import special_kind
from app.query.validation import validate


def intent(**changes: object) -> Intent:
    values = dict(
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
    values.update(changes)
    return Intent.model_validate(values)


@pytest.mark.parametrize(
    "question,period,start,end",
    [
        ("Doanh thu tháng này", "this_month", date(2025, 6, 1), date(2025, 6, 30)),
        ("Còn quý trước?", "last_quarter", date(2025, 1, 1), date(2025, 4, 1)),
        ("Revenue last month", "last_month", date(2025, 5, 1), date(2025, 6, 1)),
    ],
)
def test_relative_dates_override_wrong_model_dates(
    question: str, period: str, start: date, end: date
) -> None:
    resolved = merged_intent(intent(period="last_year"), None, question)
    assert resolved.period == period
    assert not resolved.needs_clarification
    assert resolve_period(period, date(2025, 6, 29)) == (start, end)


def test_date_only_reply_keeps_metric_and_clears_redundant_clarification() -> None:
    prior = {"slots": intent().model_dump()}
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["metric_id"]), prior, "Tháng 6 năm 2025"
    )
    assert resolved.metric_id == "revenue"
    assert (resolved.start_date, resolved.end_date) == ("2025-06-01", "2025-07-01")
    assert not resolved.needs_clarification


def test_unresolved_business_ambiguity_and_vague_dates_stay_clarifications() -> None:
    resolved = merged_intent(
        intent(missing_fields=["request"]), None, "Doanh thu và lợi nhuận tháng này"
    )
    assert resolved.needs_clarification
    assert merged_intent(intent(period="recently"), None).needs_clarification
    assert date_hints("So sánh tháng này với tháng trước") == {}


def test_incomplete_new_date_does_not_reuse_old_literal_dates() -> None:
    prior = {
        "slots": intent(
            period="explicit", start_date="2025-01-01", end_date="2025-02-01"
        ).model_dump()
    }
    resolved = merged_intent(
        intent(period="explicit", start_date="2024-01-01", missing_fields=["end_date"]),
        prior,
    )
    assert resolved.end_date is None
    assert resolved.needs_clarification


def test_vietnamese_summary_formats_actual_value_without_inventing_currency() -> None:
    summary = factual(
        "revenue", [{"revenue": "1234567.89"}], date(2025, 1, 1), date(2025, 4, 1), "vi"
    )
    assert "1.234.567,89" in summary and "31/03/2025" in summary
    assert "đơn vị tiền tệ nguồn" in summary and "VND" not in summary


def test_complete_comparisons_and_confirmations_do_not_require_date_templates() -> None:
    query = "Cho tôi chart so sánh doanh thu tháng 5 và doanh thu tháng 6 năm 2025"
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["request"]), None, query
    )
    assert (resolved.metric_id, resolved.dimension) == ("revenue", "month")
    assert (resolved.start_date, resolved.end_date) == ("2025-05-01", "2025-07-01")
    prior = {"turns": [{"question": query, "answer": "Please confirm."}]}
    confirmed = merged_intent(
        intent(metric_id=None, missing_fields=["request"]), prior, "Đúng vậy"
    )
    assert not confirmed.needs_clarification and confirmed.dimension == "month"


def test_year_and_two_territories_are_enough_information() -> None:
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=["request"]),
        None,
        "So sánh doanh thu Canada và Northwest cả năm 2024",
    )
    assert (resolved.start_date, resolved.end_date) == ("2024-01-01", "2025-01-01")
    assert resolved.dimension == "sales_territory"
    assert resolved.territory == "Canada|Northwest"
    plan = build(resolved, "manager", date(2025, 6, 29))
    validated = validate(plan, resolved, "manager", trusted_template=True)
    assert validated.params["territory_0"] == "Canada"
    assert validated.params["territory_1"] == "Northwest"
    assert ":territory_0" in validated.sql and ":territory_1" in validated.sql


def test_common_business_wording_and_data_overview_do_not_need_exact_metric_ids() -> (
    None
):
    assert (
        merged_intent(intent(metric_id=None), None, "Sản xuất tháng này").metric_id
        == "production_output"
    )
    assert (
        merged_intent(intent(metric_id=None), None, "Tỷ lệ lỗi quý trước").metric_id
        == "defect_rate"
    )
    assert (
        special_kind("Database có bao nhiêu nhà máy và những nhà máy nào?")
        == "factories"
    )
    assert special_kind("Có dữ liệu năm 2024 không?") == "coverage"
    assert special_kind("Doanh thu của từng nhà máy") == "factory_revenue"


def test_shortcut_answers_are_role_gated() -> None:
    from app.query.shortcuts import SPECIAL_ROLES

    assert "production" not in SPECIAL_ROLES["coverage"]
    assert "sales" not in SPECIAL_ROLES["factories"]
    assert "it_admin" not in {r for roles in SPECIAL_ROLES.values() for r in roles}


def test_growth_wording_maps_to_sales_growth() -> None:
    from app.core.dates import intent_hints

    for text in (
        "Doanh thu tháng trước tăng bao nhiêu so với tháng liền trước?",
        "Tăng trưởng doanh thu quý trước",
    ):
        assert intent_hints(text)["metric_id"] == "sales_growth"
    assert intent_hints("Doanh thu tháng trước")["metric_id"] == "revenue"


def test_multi_metric_and_month_pair_questions() -> None:
    from app.core.dates import intent_hints

    assert "metric_id" not in intent_hints("Tổng doanh thu và tỷ lệ phế phẩm")
    assert "metric_id" not in intent_hints("Doanh thu và lợi nhuận tháng này")
    pair = intent_hints("Doanh thu tháng 5 so với tháng 4 năm 2025")
    assert (pair["start_date"], pair["end_date"]) == ("2025-04-01", "2025-06-01")
    assert pair["dimension"] == "month"


def test_listing_reshaping_and_share_questions() -> None:
    from datetime import date

    from app.core.dates import intent_hints
    from app.presentation.summary import share_text
    from app.presentation.visualization import reshape_kind, reshaped
    from app.query.shortcuts import special_kind

    assert special_kind("Cho tôi tất cả khu vực hiện tại đang có trong dữ liệu") == (
        "territories"
    )
    assert special_kind("Cho tôi doanh thu của tất cả khu vực năm 2024") is None
    assert special_kind("Liệt kê các dây chuyền sản xuất") == "lines"
    assert reshape_kind("Đổi sang biểu đồ tròn") == "pie"
    assert reshape_kind("Hiển thị dạng bảng") == "table"
    assert reshape_kind("Cho tôi chart so sánh doanh thu tháng 5 và tháng 6") is None
    hints = intent_hints("Doanh thu Đức và Anh chiếm bao nhiêu phần trăm năm 2023")
    assert hints["territory"] == "Germany|United Kingdom"

    rows = [
        {
            "territoryid": str(i),
            "territory": f"T{i}",
            "revenue": "100",
            "sample_count": 1,
        }
        for i in range(10)
    ]
    text = share_text(rows, ["T1", "T2"], date(2023, 1, 1), date(2024, 1, 1), "vi")
    assert text is not None and "20.00%" in text and "2023-12-31" in text
    viz, note = reshaped("pie", rows, "vi")  # 10 groups: pie is not allowed
    assert viz["type"] == "bar" and "biểu đồ cột" in note
    assert viz["y"] == ["revenue"] and viz["x"] == "territory"
    assert reshaped("table", rows, "en")[0]["type"] == "table"


def test_all_chart_types_are_recognised_and_mapped() -> None:
    from app.core.dates import intent_hints
    from app.presentation.visualization import auto_viz, requested_chart

    asks = {
        "Doanh thu theo danh mục dạng biểu đồ tròn": "pie",
        "Đổi sang biểu đồ vành khuyên": "donut",
        "Biểu đồ cột chồng doanh thu": "stacked_bar",
        "Vẽ biểu đồ phân tán": "scatter",
        "Hiển thị dạng thẻ KPI": "kpi_card",
        "Doanh thu theo tháng dạng biểu đồ đường": "line",
        "Sản lượng theo production line": None,
    }
    for text, kind in asks.items():
        assert requested_chart(text) == kind, text
    pair = intent_hints("Doanh thu theo tháng và khu vực dạng cột chồng năm 2024")
    assert (pair["dimension"], pair["series_dimension"]) == ("month", "sales_territory")

    rows = [
        {"month": f"2024-0{m}-01", "territory": t, "revenue": "10", "sample_count": 2}
        for m in (1, 2)
        for t in ("A", "B")
    ]
    stacked = auto_viz("stacked_bar", rows)
    assert (stacked.x, stacked.series, stacked.y) == ("month", "territory", ["revenue"])
    assert auto_viz("scatter", rows).x == "sample_count"
    import pytest

    with pytest.raises(ValueError):
        auto_viz("kpi_card", rows)  # four rows, not one value


def test_local_intent_only_for_unambiguous_questions() -> None:
    from app.conversation.intent import local_intent

    ok = local_intent("Doanh thu theo danh mục sản phẩm năm 2024 dạng biểu đồ tròn")
    assert ok and (ok.metric_id, ok.dimension) == ("revenue", "product_category")
    factory = local_intent("Tỷ lệ phế phẩm của Factory A tháng trước")
    assert factory and factory.factory_id == 1
    for text in (
        "Top 5 sản phẩm bán chạy năm 2024",
        "Weekly revenue last month",
        "Doanh thu theo phân khúc khách hàng năm 2024",
        "Có vấn đề gì không?",
    ):
        assert local_intent(text) is None, text


def test_small_talk_does_not_replay_and_month_with_trailing_words() -> None:
    from app.conversation.intent import merged_intent

    prior = {
        "slots": {"metric_id": "production_output", "period": "explicit"},
        "turns": [],
    }
    blank = intent(metric_id=None, missing_fields=["metric_id"])
    hello = merged_intent(blank, prior, "hello")
    assert hello.metric_id is None and hello.needs_clarification
    followup = merged_intent(blank, prior, "Còn quý trước?")
    assert followup.metric_id == "production_output"

    for text in ("tháng 5 năm 2025 đi", "Doanh thu tháng 5 năm 2025"):
        hints = date_hints(text)
        assert (hints["start_date"], hints["end_date"]) == (
            "2025-05-01",
            "2025-06-01",
        ), text
    # A comparison is never collapsed to one month.
    from app.core.dates import intent_hints

    pair = intent_hints("doanh thu tháng 5 so với tháng 4 năm 2025")
    assert (pair["start_date"], pair["end_date"]) == ("2025-04-01", "2025-06-01")


def test_transcript_keeps_every_turn_in_order() -> None:
    from app.history.service import build_transcript

    def saved(i: int, question: str) -> dict:
        return {"id": f"r{i}", "question": question, "payload": {"table": []}}

    results = [saved(1, "q1"), saved(2, "q2"), saved(3, "q3"), saved(4, "q4")]
    # Context remembers only the newest turns; q1 is older than the window.
    turns = [
        {"question": "q2", "answer": ""},
        {"question": "Doanh thu", "answer": "Which period?"},
        {"question": "q3", "answer": ""},
        {"question": "q4", "answer": ""},
    ]
    transcript = build_transcript(results, turns)
    assert [t["question"] for t in transcript] == ["q1", "q2", "Doanh thu", "q3", "q4"]
    assert transcript[2]["answer"]["status"] == "needs_clarification"
    assert transcript[0]["answer"]["result_id"] == "r1"
    assert build_transcript([], []) == []


def test_unknown_factory_and_territory_are_asked_about() -> None:
    from app.conversation.intent import clarification_text, merged_intent

    factory_d = merged_intent(
        intent(
            metric_id="defect_rate", period="last_quarter", needs_clarification=False
        ),
        None,
        "Tỷ lệ phế phẩm của Factory D quý trước",
    )
    assert factory_d.needs_clarification and factory_d.missing_fields == ["factory_id"]
    assert "Factory A, B và C" in clarification_text(factory_d, "vi")

    named = merged_intent(
        intent(territory="Đức và Pháp", needs_clarification=False, period="last_year"),
        None,
        "Doanh thu của Đức và Pháp năm ngoái",
    )
    assert named.territory == "France|Germany"
    asia = merged_intent(
        intent(territory="Asia", needs_clarification=False, period="last_year"),
        None,
        "Doanh thu Asia năm ngoái",
    )
    assert asia.missing_fields == ["territory"]


def test_unresolved_turn_keeps_earlier_slots() -> None:
    from app.query.orchestrator import carry_slots

    prior = {"slots": {"metric_id": "revenue", "period": "last_month"}}
    blank = intent(metric_id=None, period=None, factory_id=4)
    kept = carry_slots(prior, blank)
    assert kept["metric_id"] == "revenue" and kept["period"] == "last_month"
    assert "factory_id" not in kept  # an invalid factory never becomes a slot
    assert carry_slots(prior, intent(period="last_quarter"))["period"] == "last_quarter"


def test_named_countries_filter_the_answer() -> None:
    from app.conversation.intent import local_intent
    from app.core.dates import intent_hints

    both = local_intent("cho tôi doanh thu của Đức và Pháp năm 2025 đi")
    assert both and both.territory == "France|Germany"
    assert both.dimension == "sales_territory"
    one = local_intent("Doanh thu năm 2024 của Canada")
    assert one and one.territory == "Canada" and one.dimension == "none"
    # "Anh" alone is ambiguous (the UK, or a form of address): the model decides.
    assert local_intent("Doanh thu của Anh năm 2024") is None
    assert "territory" not in intent_hints("anh ơi cho em xem doanh thu tháng này")
    assert intent_hints("Doanh thu của Đức và Anh năm 2023")["territory"] == (
        "Germany|United Kingdom"
    )


def test_answers_read_the_rows_instead_of_repeating_a_template() -> None:
    from datetime import date

    from app.presentation.analysis import analysis_kind, analyze
    from app.presentation.summary import factual

    rows = [
        {"territory": "France", "revenue": "1664041.62", "sample_count": 1030},
        {"territory": "Germany", "revenue": "1548206.97", "sample_count": 1048},
    ]
    text = analyze("higher", rows, "revenue", "vi")
    assert text is not None
    assert text.startswith("France cao hơn Germany: 1.664.041,62 so với 1.548.206,97")
    assert "115.834,65" in text and "+7,5%" in text
    assert "Germany thấp hơn" not in text
    lower = analyze("lower", rows, "revenue", "vi")
    assert lower is not None and lower.startswith("Germany thấp hơn France")
    total = analyze("total", rows, "revenue", "vi")
    assert total is not None and "3.212.248,59" in total
    assert analysis_kind("giữa 2 cái thì cái nào nhiều hơn") == "higher"
    assert analysis_kind("tổng cộng là bao nhiêu") == "total"
    assert analysis_kind("Chào bạn") is None
    summary = factual("revenue", rows, date(2025, 1, 1), date(2026, 1, 1), "vi")
    assert "France cao hơn Germany" in summary and "Chi tiết trong bảng" not in summary


def test_internal_errors_never_reach_the_user() -> None:
    from app.presentation.messages import Explained, friendly

    known = friendly(ValueError("Specify both start and end dates"), "vi")
    assert "từ ngày nào" in known
    assert "start and end" not in known
    odd = friendly(ValueError("KeyError: 'salesorderid' at line 42"), "en")
    assert "salesorderid" not in odd and "line 42" not in odd
    assert friendly(Explained("Custom note"), "vi") == "Custom note"


def test_definitions_and_small_talk_come_from_fixed_vocabulary() -> None:
    from pathlib import Path

    import yaml
    from app.conversation.glossary import converse

    path = Path(__file__).resolve().parents[3] / "data/business_dictionary"
    dictionary = yaml.safe_load((path / "dictionary.yaml").read_text("utf-8"))
    de = converse("germany trong dữ liệu bạn nói tiếng việt là gì", "vi", dictionary)
    assert de and "Germany là Đức" in de
    rev = converse("Doanh thu là gì", "vi", dictionary)
    assert (
        rev and "SUM(sales.salesorderheader.subtotal)" in rev and "Phiên bản 1" in rev
    )
    assert converse("Chào bạn", "vi", dictionary)
    assert converse("bạn làm được gì", "vi", dictionary)
    assert converse("Doanh thu tháng này là gì", "vi", dictionary) is None
    assert converse("Doanh thu theo khu vực năm 2024", "vi", dictionary) is None


def test_forecast_requests_are_declined_not_answered_as_empty_data() -> None:
    from app.conversation.glossary import asks_forecast

    asked = (
        "Dựa trên doanh thu của đức và pháp thì bạn có dự đoán được trong năm 2026 "
        "doanh thu sẽ phát triển theo hướng nào",
        "Dự báo sản lượng tháng tới",
        "Vật tư nào có nguy cơ thiếu cho kế hoạch sản xuất tuần tới?",
        "Forecast revenue for next year",
        "Doanh thu quý sau sẽ ra sao",
    )
    for text in asked:
        assert asks_forecast(text), text
    recorded = (
        "Doanh thu năm 2024",
        "Xu hướng doanh thu theo tháng năm 2024",
        "Doanh thu năm ngoái",
        "Doanh thu năm 2030",  # a named future year is still a plain no-data query
        "Sản lượng tháng trước",
    )
    for text in recorded:
        assert not asks_forecast(text), text


def test_efficiency_and_unsupported_breakdowns_ask_instead_of_guessing() -> None:
    from app.conversation.glossary import asks_materials
    from app.conversation.intent import clarification_text, merged_intent

    blank = intent(
        metric_id="production_output", period="today", needs_clarification=False
    )
    eff = merged_intent(
        blank, None, "Hiệu suất các dây chuyền sản xuất hôm nay như thế nào?"
    )
    assert eff.missing_fields == ["efficiency"]
    assert "hoàn thành đúng hạn" in clarification_text(eff, "vi")
    named = merged_intent(blank, None, "Sản lượng hôm nay, hiệu suất theo sản lượng")
    assert named.missing_fields != ["efficiency"]

    by_staff = merged_intent(
        intent(period="last_year", needs_clarification=False),
        None,
        "Doanh thu theo nhân viên bán hàng năm 2024",
    )
    assert by_staff.missing_fields == ["dimension"]
    assert "nhân viên bán hàng" in clarification_text(by_staff, "vi")
    assert asks_materials("Vật tư nào có nguy cơ thiếu cho kế hoạch sản xuất tuần tới?")
    assert not asks_materials("Sản lượng tuần trước")


def test_on_time_rate_is_a_template_metric_with_its_own_wording() -> None:
    from datetime import date

    from app.conversation.intent import local_intent
    from app.query.builder import build, supports

    on_time = intent(metric_id="on_time_rate", period="last_month")
    assert supports(on_time)
    plan = build(on_time, "production", date(2025, 6, 29))
    assert "w.enddate<=w.duedate" in plan.sql and "on_time_rate" in plan.sql
    hints_local = local_intent("Tỷ lệ hoàn thành đúng hạn theo dây chuyền năm 2024")
    assert hints_local and hints_local.metric_id == "on_time_rate"
    assert hints_local.dimension == "production_line"
    today = local_intent("Sản lượng hôm nay")
    assert today and today.period == "today"
