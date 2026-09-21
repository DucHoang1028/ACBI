from datetime import date

import pytest
from app.ai.client import Intent
from app.conversation.intent import merged_intent
from app.core.dates import date_hints, resolve_period
from app.presentation.summary import factual
from app.query.builder import build
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


def test_growth_wording_maps_to_sales_growth() -> None:
    from app.core.dates import intent_hints

    for text in (
        "Doanh thu tháng trước tăng bao nhiêu so với tháng liền trước?",
        "Tăng trưởng doanh thu quý trước",
    ):
        assert intent_hints(text)["metric_id"] == "sales_growth"
    assert intent_hints("Doanh thu tháng trước")["metric_id"] == "revenue"


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


def test_unresolved_turn_keeps_earlier_slots() -> None:
    from app.query.orchestrator import carry_slots

    prior = {"slots": {"metric_id": "revenue", "period": "last_month"}}
    blank = intent(metric_id=None, period=None, factory_id=4)
    kept = carry_slots(prior, blank)
    assert kept["metric_id"] == "revenue" and kept["period"] == "last_month"
    assert "factory_id" not in kept  # an invalid factory never becomes a slot
    assert carry_slots(prior, intent(period="last_quarter"))["period"] == "last_quarter"


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


def test_the_models_intent_type_decides_routing_and_is_not_overwritten() -> None:
    from app.conversation.intent import merged_intent

    for kind in ("unsupported", "metadata", "chat", "forecast"):
        raw = intent(metric_id=None, intent_type=kind, needs_clarification=False)
        assert merged_intent(raw, None, "Doanh thu và lợi nhuận tháng này") is raw
    vague = merged_intent(
        intent(metric_id="revenue", period="recently"), None, "Doanh thu gần đây"
    )
    assert vague.period == "recently"  # the pipeline asks for a period, as before


def test_hints_fill_gaps_but_never_overrule_the_model() -> None:
    from app.conversation.intent import merged_intent

    model_says = intent(metric_id="defect_rate", period="last_month")
    kept = merged_intent(model_says, None, "Doanh thu tháng này")
    assert kept.metric_id == "defect_rate"  # a keyword does not beat the model
    assert kept.period == "this_month"  # calendar wording is the backend's job
    gap = merged_intent(intent(metric_id=None), None, "Sản lượng tháng này")
    assert gap.metric_id == "production_output"


def test_vocabulary_comes_from_the_dictionary_not_from_code() -> None:
    from app.core.dates import intent_hints, single_dimension, territory_names
    from app.core.text import fold
    from app.metadata import vocabulary

    assert (
        intent_hints("Doanh thu và tỷ lệ phế phẩm quý trước").get("metric_id") is None
    )
    assert (
        intent_hints("Tỷ lệ hoàn thành đúng hạn quý trước")["metric_id"]
        == "on_time_rate"
    )
    assert single_dimension(fold("theo dây chuyền")) == "production_line"
    assert single_dimension(fold("theo tháng")) == "month"
    assert territory_names("germany and france") == ["France", "Germany"]
    assert territory_names("Asia") is None
    vocab = vocabulary.get()
    assert vocab.unknown_member_reference("ty le phe pham cua factory d") == "factory"
    assert vocab.unknown_member_reference("ty le phe pham cua nha may a") is None
    vocab.members["sales_territory"] = ["Atlantis"]  # a different database
    try:
        assert territory_names("atlantis") == ["Atlantis"]
        assert territory_names("germany") is None
    finally:
        vocab.members["sales_territory"] = [
            "Australia",
            "Canada",
            "Central",
            "France",
            "Germany",
            "Northeast",
            "Northwest",
            "Southeast",
            "Southwest",
            "United Kingdom",
        ]


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
    assert "Factory A" in clarification_text(factory_d, "vi")
    named = merged_intent(
        intent(
            territory="germany and france",
            needs_clarification=False,
            period="last_year",
        ),
        None,
        "Doanh thu của Germany và France năm ngoái",
    )
    assert named.territory == "France|Germany"
    asia = merged_intent(
        intent(territory="Asia", needs_clarification=False, period="last_year"),
        None,
        "Doanh thu Asia năm ngoái",
    )
    assert asia.missing_fields == ["territory"]
    bad_id = merged_intent(
        intent(factory_id=9, metric_id="production_output", period="last_month"),
        None,
        "Sản lượng nhà máy 9 tháng trước",
    )
    assert bad_id.missing_fields == ["factory_id"]


def test_growth_wording_settles_the_metric_even_when_the_model_reads_revenue() -> None:
    from app.conversation.intent import merged_intent
    from app.core.dates import intent_hints, is_confirmation

    said = intent(metric_id="revenue", period="this_month", needs_clarification=False)
    resolved = merged_intent(
        said, None, "Doanh thu tháng này so với tháng trước thế nào?"
    )
    assert resolved.metric_id == "sales_growth" and not resolved.needs_clarification
    asked = merged_intent(
        intent(metric_id="revenue", period="this_month", needs_clarification=True),
        None,
        "Doanh thu tháng này so với tháng trước",
    )
    assert asked.metric_id == "sales_growth"
    # Without that wording the model's own reading stands.
    plain = merged_intent(
        intent(metric_id="defect_rate", period="this_month", needs_clarification=False),
        None,
        "Doanh thu tháng này",
    )
    assert plain.metric_id == "defect_rate"
    assert "metric_override" not in intent_hints("Doanh thu tháng này")
    assert is_confirmation("Đúng vậy") and is_confirmation("ok")


def test_an_open_ended_period_runs_through_the_latest_data() -> None:
    from datetime import date

    from app.query.builder import resolve_dates

    open_end = intent(period="explicit", start_date="2022-01-01", end_date=None)
    assert resolve_dates(open_end, date(2025, 6, 29)) == (
        date(2022, 1, 1),
        date(2025, 6, 30),
    )
    with pytest.raises(ValueError):
        resolve_dates(intent(period="explicit"), date(2025, 6, 29))


def test_answering_a_clarification_resumes_the_request_and_never_repeats_it() -> None:
    from datetime import date

    from app.conversation.intent import resume_pending, unstick
    from app.query.orchestrator import carry_slots, horizon_to

    asked = intent(metric_id="production_output", intent_type="forecast")
    slots = carry_slots(None, asked)
    assert slots["intent_type"] == "forecast"
    # Plain data requests are not remembered as a kind.
    assert "intent_type" not in carry_slots(None, intent(intent_type="metric_query"))

    prior = {"slots": slots, "pending_question": "Which period?"}
    answer = intent(metric_id="production_output", intent_type="metric_query")
    assert resume_pending(answer, prior).intent_type == "forecast"
    assert resume_pending(answer, {"slots": {}, "pending_question": None}) is answer
    assert resume_pending(answer, {"slots": slots, "pending_question": None}) is answer

    same = "Bạn muốn xem kỳ nào?"
    assert unstick(same, {"pending_question": same}, "vi") != same
    assert unstick(same, {"pending_question": "khác"}, "vi") == same
    assert unstick(same, None, "vi") == same

    # "for 2026" means through December 2026, counted from the first forecast month.
    assert horizon_to("năm 2026 đi", date(2025, 6, 1)) == 19
    assert horizon_to("dự báo giúp tôi", date(2025, 6, 1)) is None


def test_a_short_reply_picks_one_of_the_offered_options() -> None:
    from app.conversation.intent import chosen_option

    offered = (
        "Bạn muốn làm yêu cầu nào trước: (1) xu hướng doanh thu của Úc, "
        "hay (2) dự báo sản lượng? Vui lòng chọn một."
    )
    trend = "xu hướng doanh thu của Úc"
    forecast = "dự báo sản lượng"
    for reply in (
        "1 đi",
        "Cả 2 cùng lúc",
        "cái đầu tiên",
        "Thực hiện yêu cầu đầu tiên trước",
    ):
        assert chosen_option(reply, offered) == trend, reply
    for reply in ("2", "số 2 nhé", "yêu cầu thứ hai"):
        assert chosen_option(reply, offered) == forecast, reply
    # Anything else is a new message, and without options there is nothing to pick.
    assert chosen_option("doanh thu năm 2024", offered) is None
    assert chosen_option("1 đi", "Bạn muốn xem kỳ nào?") is None
    assert chosen_option("1 đi", None) is None


def test_tolerant_option_picking_and_a_new_number_is_a_new_request() -> None:
    from app.conversation.intent import chosen_option

    offered = "Chọn: (1) xu hướng doanh thu, hay (2) dự báo sản lượng?"
    for reply in ("1 trước đi", "Thực hiện yêu cầu 1 trước", "làm cái 1 nhé"):
        assert chosen_option(reply, offered) == "xu hướng doanh thu", reply
    assert chosen_option("doanh thu năm 2024 của 3 nhà máy", offered) is None
    assert chosen_option("3", offered) is None


def test_requests_left_for_later_are_named_in_the_answer() -> None:
    from app.query.orchestrator import note_deferred

    raw = intent(deferred_requests=["dự báo sản lượng 3 tháng"])
    result = {"status": "ok", "answer_text": "Doanh thu là 10."}
    note_deferred(result, raw, "vi")
    assert "dự báo sản lượng 3 tháng" in result["answer_text"]
    assert result["answer_text"].startswith("Doanh thu là 10.")
    refused = {"status": "denied", "message": "Không có quyền."}
    note_deferred(refused, raw, "vi")
    assert "answer_text" not in refused
    untouched = {"status": "ok", "answer_text": "Xong."}
    note_deferred(untouched, intent(), "vi")
    assert untouched["answer_text"] == "Xong."


def test_a_short_reply_asks_for_the_request_left_for_later() -> None:
    from app.conversation.intent import next_deferred

    prior = {"slots": {"deferred_requests": ["dự báo sản lượng", "so sánh nhà máy"]}}
    for reply in ("tiếp đi", "Cả 2 cùng lúc", "Bây giờ thực hiện yêu cầu thứ 2"):
        assert next_deferred(reply, prior) == ("dự báo sản lượng", ["so sánh nhà máy"])
    assert next_deferred("doanh thu năm 2024", prior) == (None, [])
    assert next_deferred("tiếp đi", {"slots": {}}) == (None, [])
    assert next_deferred("tiếp đi", None) == (None, [])


def test_standalone_question_does_not_inherit_earlier_filters() -> None:
    """France versus Germany in the last turn must not filter "Doanh thu năm 2024"."""
    prior = {
        "slots": intent(
            dimension="sales_territory",
            territory="France|Germany",
            period="explicit",
            start_date="2024-01-01",
            end_date="2025-01-01",
        ).model_dump()
    }
    copied = intent(
        dimension="sales_territory",
        territory="France|Germany",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
    )
    resolved = merged_intent(copied, prior, "Doanh thu năm 2024 là bao nhiêu?")
    assert resolved.territory is None
    assert resolved.dimension == "none"
    assert resolved.metric_id == "revenue"


def test_short_follow_up_still_inherits_earlier_filters() -> None:
    prior = {
        "slots": intent(
            dimension="sales_territory",
            territory="Canada",
            period="explicit",
            start_date="2024-01-01",
            end_date="2025-01-01",
        ).model_dump()
    }
    resolved = merged_intent(
        intent(metric_id=None, missing_fields=[]), prior, "Còn năm 2023 thì sao?"
    )
    assert resolved.territory == "Canada"


def test_named_factory_in_a_deferred_request_is_not_an_unknown_factory() -> None:
    raw = intent(
        territory="Canada",
        period="last_month",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
        deferred_requests=["sản lượng Factory B"],
    )
    resolved = merged_intent(
        raw, None, "Doanh thu Canada tháng trước, sản lượng Factory B"
    )
    assert not resolved.needs_clarification
    assert resolved.territory == "Canada"


def test_vague_message_does_not_replay_the_previous_query() -> None:
    prior = {
        "slots": intent(period="last_month", territory="Canada").model_dump(),
        "turns": [],
    }
    copied = intent(
        period="last_month",
        territory="Canada",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
    )
    assert merged_intent(copied, prior, "cho tôi xem số liệu").needs_clarification


def test_comparing_with_another_territory_keeps_both() -> None:
    prior = {
        "slots": intent(
            territory="Canada", period="last_year", dimension="month"
        ).model_dump()
    }
    raw = intent(
        territory="Australia",
        period="last_year",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
    )
    resolved = merged_intent(raw, prior, "So với Úc thì ai cao hơn?")
    assert set(resolved.territory.split("|")) == {"Canada", "Australia"}
    assert resolved.dimension == "sales_territory"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Doanh thu Q1 2025 so với Q1 2024", True),
        ("Tăng trưởng năm 2024 so với 2023 là bao nhiêu?", True),
        ("Doanh thu năm 2024", False),
        ("Doanh thu từ năm 2022 đến 2025 tăng trưởng thế nào", False),
        ("Doanh thu Đức Pháp Anh Úc năm 2024 so sánh giúp mình", False),
    ],
)
def test_two_period_comparison_is_detected(question: str, expected: bool) -> None:
    from app.core.dates import compares_two_periods

    assert compares_two_periods(question) is expected


def test_comparison_wording_with_two_territories_breaks_down_by_territory() -> None:
    prior = {"slots": intent(territory="Canada", dimension="month").model_dump()}
    raw = intent(
        territory="Canada|Australia",
        dimension="month",
        period="last_year",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
    )
    resolved = merged_intent(raw, prior, "So với Úc thì ai cao hơn?")
    assert resolved.dimension == "sales_territory"


def test_one_row_result_names_its_group() -> None:
    rows = [{"productid": 807, "product": "HL Headset", "defect_rate": "0.0080"}]
    text = factual("defect_rate", rows, date(2024, 1, 1), date(2025, 1, 1), "vi")
    assert "HL Headset" in text
    assert "HL Headset" not in factual(
        "revenue", [{"revenue": "10"}], date(2024, 1, 1), date(2025, 1, 1), "vi"
    )


def test_revenue_filters_are_not_inherited_by_production_output() -> None:
    prior = {
        "slots": intent(
            territory="Canada", dimension="sales_territory", period="last_month"
        ).model_dump()
    }
    raw = intent(
        metric_id="production_output",
        needs_clarification=False,
        clarification_question=None,
        missing_fields=[],
    )
    resolved = merged_intent(raw, prior, "Sản lượng của Factory B thì sao")
    assert resolved.territory is None
    assert resolved.dimension == "none"


def test_share_text_uses_vietnamese_number_format() -> None:
    from app.presentation.summary import share_text

    rows = [
        {"territory": "France", "revenue": "3816543.33"},
        {"territory": "Germany", "revenue": "2570269.35"},
        {"territory": "Canada", "revenue": "6231209.96"},
    ]
    text = share_text(rows, ["France"], date(2024, 1, 1), date(2025, 1, 1), "vi")
    assert text is not None
    assert "3.816.543,33" in text
    assert "31/12/2024" in text


def test_chart_type_follows_the_shape_of_the_result() -> None:
    from app.presentation.visualization import pick_viz

    territories = [{"territory": t, "revenue": str(v)} for t, v in (("A", 5), ("B", 3))]
    months = [{"month": f"2024-0{m}-01", "revenue": str(m)} for m in range(1, 6)]
    many = [{"territory": f"T{i}", "revenue": str(i + 1)} for i in range(9)]
    ratio = [{"factory": f, "defect_rate": "0.1"} for f in ("A", "B", "C")]
    stacked = [
        {"month": f"2024-0{m}-01", "territory": t, "revenue": "1"}
        for m in (1, 2)
        for t in ("A", "B")
    ]
    assert pick_viz([{"revenue": "1"}], "revenue", False).type == "kpi_card"
    assert pick_viz(territories, "revenue", False).type == "pie"
    assert pick_viz(territories, "revenue", True).type == "bar"  # a top-N cut
    assert pick_viz(months, "revenue", False).type == "line"
    assert pick_viz(many, "revenue", False).type == "bar"
    assert pick_viz(ratio, "defect_rate", False).type == "bar"
    assert pick_viz(stacked, "revenue", False).type == "stacked_bar"
    five = [{"territory": f"T{i}", "revenue": str(i + 1)} for i in range(5)]
    assert pick_viz(five, "revenue", False).type == "donut"
