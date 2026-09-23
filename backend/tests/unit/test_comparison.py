from datetime import date

import pytest
from app.conversation.intent import (
    first_task_text,
    further_requests,
    local_comparison_intent,
)
from app.core.dates import named_periods
from app.query.comparison import adjacent, label, side_by_side


@pytest.mark.parametrize(
    "question,expected",
    [
        (
            "so sánh doanh thu canada năm 2023 và 2024",
            [("year", date(2023, 1, 1)), ("year", date(2024, 1, 1))],
        ),
        (
            "Doanh thu năm 2024 so với 2023",
            [("year", date(2023, 1, 1)), ("year", date(2024, 1, 1))],
        ),
        (
            "Doanh thu Q1 2025 so với quý 1 năm 2024",
            [("quarter", date(2024, 1, 1)), ("quarter", date(2025, 1, 1))],
        ),
        (
            "revenue March 2025 vs March 2024",
            [("month", date(2024, 3, 1)), ("month", date(2025, 3, 1))],
        ),
        (
            "so sánh tháng 3 năm 2024 và tháng 3/2023",
            [("month", date(2023, 3, 1)), ("month", date(2024, 3, 1))],
        ),
        ("what is the market 2024", [("year", date(2024, 1, 1))]),
        (
            "so sánh doanh thu quý 1 và quý 2 năm 2025",
            [("quarter", date(2025, 1, 1)), ("quarter", date(2025, 4, 1))],
        ),
        (
            "Q1 vs Q2 2024",
            [("quarter", date(2024, 1, 1)), ("quarter", date(2024, 4, 1))],
        ),
        (
            "so sánh tháng 1, tháng 2 và tháng 3 năm 2025",
            [
                ("month", date(2025, 1, 1)),
                ("month", date(2025, 2, 1)),
                ("month", date(2025, 3, 1)),
            ],
        ),
        ("doanh thu tháng 3 so với tháng trước", []),
    ],
)
def test_named_periods(question: str, expected: list[tuple[str, date]]) -> None:
    assert [(k, s) for k, s, _ in named_periods(question)] == expected


def test_period_bounds_are_half_open() -> None:
    ((_, start, end),) = named_periods("Q4 2024")
    assert (start, end) == (date(2024, 10, 1), date(2025, 1, 1))
    assert adjacent(named_periods("2023 và 2024"))
    assert not adjacent(named_periods("2022 và 2024"))
    assert label(("quarter", start, end), "vi") == "Quý 4/2024"


def test_side_by_side_reads_change_from_the_rows() -> None:
    rows = [
        ("Năm 2023", [{"revenue": "100.00", "sample_count": 4}]),
        ("Năm 2024", [{"revenue": "125.00", "sample_count": 5}]),
    ]
    table, text = side_by_side("revenue", rows, None, "Canada", "vi")
    assert [r["period"] for r in table] == ["Năm 2023", "Năm 2024"]
    assert "tăng 25,0%" in text and "Canada" in text
    _, english = side_by_side("revenue", rows, None, "", "en")
    assert "up 25.0%" in english


def test_side_by_side_needs_two_periods_with_data() -> None:
    table, text = side_by_side(
        "revenue",
        [("Year 2023", [{"revenue": "1", "sample_count": 1}]), ("Year 2030", [])],
        None,
        "",
        "en",
    )
    assert table == [] and "Year 2030" in text


def test_side_by_side_by_territory_pivots_periods_into_columns() -> None:
    rows = [
        (
            "Year 2023",
            [
                {"territory": "Canada", "revenue": "10"},
                {"territory": "France", "revenue": "40"},
            ],
        ),
        (
            "Year 2024",
            [
                {"territory": "Canada", "revenue": "20"},
                {"territory": "France", "revenue": "30"},
            ],
        ),
    ]
    table, text = side_by_side("revenue", rows, "territory", "", "en")
    assert table[0]["territory"] == "France"  # largest in the latest period first
    assert table[1] == {"territory": "Canada", "Year 2023": "10", "Year 2024": "20"}
    assert "Biggest rise: Canada (+100.0%)" in text


def test_local_comparison_intent_reads_metric_filter_and_breakdown() -> None:
    one = local_comparison_intent("so sánh doanh thu canada năm 2023 và 2024")
    assert one is not None
    assert (one.metric_id, one.territory, one.dimension) == (
        "revenue",
        "Canada",
        "none",
    )
    two = local_comparison_intent("so sánh doanh thu Canada và France năm 2023 và 2024")
    assert two is not None and two.dimension == "sales_territory"
    assert two.territory == "Canada|France"


def test_local_comparison_intent_leaves_unclear_wording_to_the_model() -> None:
    for question in (
        "so sánh doanh thu của Wakanda năm 2023 và 2024",  # a name not in the data
        "so sánh doanh thu và sản lượng năm 2023 và 2024",  # two metrics
        "dự báo doanh thu 2026 so với 2025",  # a forecast
        "doanh thu năm 2024",  # no comparison
    ):
        assert local_comparison_intent(question) is None, question


def test_a_period_after_and_stays_with_its_own_request() -> None:
    question = (
        "so sánh doanh thu năm 2023 và 2024 và so sánh sản lượng năm 2023 và 2024"
    )
    later = further_requests(question)
    assert later == ["so sánh sản lượng năm 2023 và 2024"]
    assert first_task_text(question, later) == "so sánh doanh thu năm 2023 và 2024"
    plain = "so sánh doanh thu Canada năm 2023 và 2024, và dự báo doanh thu 6 tháng tới"
    assert further_requests(plain) == ["dự báo doanh thu 6 tháng tới"]
    assert first_task_text(plain, further_requests(plain)) == (
        "so sánh doanh thu Canada năm 2023 và 2024"
    )


def test_unknown_proper_names_are_asked_about_never_dropped() -> None:
    from app.ai.client import Intent
    from app.conversation.intent import validate_choices

    def ask(question: str, **fields: object) -> Intent:
        base = {
            "metric_id": "revenue",
            "dimension": "none",
            "period": "explicit",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "factory_id": None,
            "territory": None,
            "limit": 100,
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
        } | fields
        return validate_choices(Intent.model_validate(base), question)

    assert ask("Doanh thu Wakanda năm 2024").missing_fields == ["territory"]
    assert ask("Doanh thu năm 2024 của Wakanda").needs_clarification
    for fine in (
        "Doanh thu năm 2024",
        "Doanh thu United Kingdom năm 2024",
        "Top 3 Territories by Revenue in 2024",
        "So sánh Doanh Thu năm 2024",
        "Revenue in March 2025 vs March 2024",
    ):
        assert not ask(fine).needs_clarification, fine
    assert not ask("Doanh thu Canada năm 2024", territory="Canada").needs_clarification


def test_split_tasks_groups_clauses_into_requests() -> None:
    from app.conversation.intent import split_tasks

    assert split_tasks(
        "so sánh doanh thu 2023 và 2024, sau đó dự báo doanh thu và top 3 sản phẩm "
        "bán chạy 2024"
    ) == [
        "so sánh doanh thu 2023 và 2024",
        "dự báo doanh thu",
        "top 3 sản phẩm bán chạy 2024",
    ]
    assert split_tasks(
        "compare Canada revenue 2023 vs 2024, forecast revenue, and show revenue by "
        "product category for 2024"
    ) == [
        "compare Canada revenue 2023 vs 2024",
        "forecast revenue",
        "show revenue by product category for 2024",
    ]
    assert (
        len(
            split_tasks(
                "doanh thu 2024, sản lượng 2024, doanh thu theo khu vực 2024, "
                "dự báo doanh thu"
            )
        )
        == 4
    )
    # one request: a second period, a repeated metric, a breakdown or a chart
    for one in (
        "so sánh doanh thu năm 2023 và doanh thu năm 2024",
        "doanh thu Canada và doanh thu France năm 2024",
        "Doanh thu năm 2024 theo khu vực và xem theo tháng",
        "Doanh thu quý trước, chia theo khu vực, top 3, cột",
    ):
        assert split_tasks(one) == [one], one
    assert split_tasks(
        "so sánh doanh thu 2023 và 2024 rồi cho tôi biết khu vực nào cao nhất năm 2024"
    )[1:] == ["cho tôi biết khu vực nào cao nhất năm 2024"]


def test_a_list_of_members_and_a_meanwhile_clause_split_correctly() -> None:
    from app.conversation.intent import split_tasks

    question = (
        "doanh số của 2 nước là Canada và France từ năm 2022 đến nay tăng như nào "
        "đồng thời dự đoán sản lượng tương lai"
    )
    tasks = split_tasks(question)
    assert len(tasks) == 2
    assert "Canada và France" in tasks[0] and tasks[1].startswith("dự đoán sản lượng")


def test_small_talk_gets_a_fixed_reply_without_a_model() -> None:
    from app.conversation.dialogue import small_talk

    for message in ("ok", "Ok nhé", "cảm ơn", "thanks!", "xin chào", "thế thôi à"):
        assert small_talk(message, "vi", "manager"), message
    for message in (
        "doanh thu năm 2024",
        "ok cho mình xem doanh thu",
        "cảm ơn, còn Canada?",
    ):
        assert small_talk(message, "vi", "manager") is None, message


def test_a_factory_number_that_does_not_exist_is_an_unknown_reference() -> None:
    from app.metadata import vocabulary

    vocab = vocabulary.get()
    for text in ("san luong nha may so 7 nam 2024", "san luong nha may 7", "factory d"):
        assert vocab.unknown_member_reference(text), text
    for text in ("san luong factory a", "san luong nha may b nam 2024"):
        assert not vocab.unknown_member_reference(text), text


def test_is_it_the_highest_compares_with_every_territory() -> None:
    from app.ai.client import Intent
    from app.conversation.intent import merged_intent

    raw = Intent.model_validate(
        {
            "metric_id": "revenue", "dimension": "none", "period": "explicit",
            "start_date": "2024-01-01", "end_date": "2025-01-01", "factory_id": None,
            "territory": "Canada", "limit": 100, "needs_clarification": False,
            "clarification_question": None, "zero_scrap_only": False,
        }
    )  # fmt: skip
    resolved = merged_intent(
        raw, None, "Có phải Canada là khu vực có doanh thu cao nhất năm 2024 không?"
    )
    assert resolved.dimension == "sales_territory" and resolved.territory is None
    top1 = raw.model_copy(update={"dimension": "sales_territory", "limit": 1})
    again = merged_intent(
        top1, None, "Có phải Canada là khu vực có doanh thu cao nhất năm 2024 không?"
    )
    assert again.territory is None and again.limit == 100  # not "top 1 for Canada"
    plain = merged_intent(raw, None, "Doanh thu Canada năm 2024")
    assert plain.dimension == "none" and plain.territory == "Canada"


def test_two_territories_by_month_stacked_is_months_stacked_by_territory() -> None:
    from app.ai.client import Intent
    from app.conversation.intent import merged_intent

    raw = Intent.model_validate(
        {
            "metric_id": "revenue", "dimension": "sales_territory",
            "period": "explicit",
            "start_date": "2024-01-01", "end_date": "2025-01-01", "factory_id": None,
            "territory": "Canada|France", "limit": 100, "needs_clarification": False,
            "clarification_question": None, "zero_scrap_only": False,
        }
    )  # fmt: skip
    resolved = merged_intent(
        raw, None, "Doanh thu theo tháng năm 2024 của Canada và France dạng cột chồng"
    )
    assert (resolved.dimension, resolved.series_dimension) == (
        "month",
        "sales_territory",
    )


def test_two_years_with_nothing_joining_them_are_asked_about() -> None:
    from app.core.dates import conflicting_years

    assert conflicting_years("doanh thu 2025 năm 2024") == ["2024", "2025"]
    for fine in (
        "doanh thu năm 2024",
        "so sánh doanh thu 2023 và 2024",
        "doanh thu từ 2022 đến 2025",
        "doanh thu 2023, 2024",
        "revenue 2024 vs 2023",
    ):
        assert conflicting_years(fine) == [], fine


def test_a_correction_in_the_message_wins() -> None:
    from app.conversation.intent import resolve_corrections

    assert resolve_corrections("doanh thu tháng 3, ý mình là tháng 4 năm 2025") == (
        "doanh thu tháng 4 năm 2025"
    )
    assert (
        resolve_corrections(
            "chào bạn, mình muốn xem doanh thu, "
            "à mà thôi xem sản lượng đi, năm 2024 nhé"
        )
        == "xem sản lượng đi, năm 2024 nhé"
    )
    plain = "doanh thu năm 2024 của Canada"
    assert resolve_corrections(plain) == plain
    assert resolve_corrections("doanh thu năm 2024, nhầm") == "doanh thu năm 2024, nhầm"


def test_percent_of_total_and_unit_requests_are_recognised() -> None:
    import re

    from app.core.dates import FORMAT_REQUEST, is_share_question
    from app.core.text import fold

    assert is_share_question("% Canada trong tổng doanh thu năm 2024")
    assert is_share_question("Canada chiếm bao nhiêu phần trăm")
    assert not is_share_question("Doanh thu Canada năm 2024")
    for asks in (
        "doanh thu năm 2024 tính bằng triệu",
        "làm tròn giúp mình",
        "revenue in millions",
    ):
        assert re.search(FORMAT_REQUEST, fold(asks)), asks
    assert not re.search(FORMAT_REQUEST, fold("doanh thu dạng bảng"))


def test_unsupported_topics_are_refused_without_a_model() -> None:
    from app.conversation.intent import local_unsupported

    for asked in (
        "Lợi nhuận năm 2024 là bao nhiêu?",
        "Top 5 nhân viên bán hàng năm 2024",
        "Quy đổi doanh thu năm 2024 sang VND",
        "Doanh thu theo khách hàng năm 2024",
        "Tồn kho hiện tại của xe đạp Mountain",
        "What is our profit margin?",
    ):
        found = local_unsupported(asked)
        assert found is not None and found.intent_type == "unsupported", asked
    for fine in (
        "Sản lượng năm 2024",  # "lượng" must not read as "lương" (salary)
        "Doanh thu và lợi nhuận năm 2024",  # a known metric is still answered
        "Doanh thu Canada năm 2024",
        "Tỷ lệ phế phẩm theo lý do",
    ):
        assert local_unsupported(fine) is None, fine


def test_how_many_percent_higher_reads_as_a_gap_not_a_share() -> None:
    from app.presentation.analysis import analysis_kind

    assert (
        analysis_kind("Khu vực đầu bảng cao hơn khu vực cuối bảng bao nhiêu phần trăm?")
        == "difference"
    )
    assert analysis_kind("Canada chiếm bao nhiêu phần trăm?") == "share"


def test_dates_the_calendar_does_not_have_are_caught() -> None:
    from app.core.dates import impossible_date

    for bad in (
        "Tỷ lệ đúng hạn tháng 13 năm 2024",
        "Doanh thu ngày 31/02/2024",
        "Doanh thu 30/2",
        "Doanh thu 2024-02-31",
        "revenue in month 14",
    ):
        assert impossible_date(bad), bad
    for fine in (
        "Doanh thu tháng 12 năm 2024",
        "Doanh thu ngày 29/02/2024",
        "Doanh thu 6 tháng tới",
        "Doanh thu tháng 3/2025",
        "Doanh thu từ 01/03/2024 đến 31/03/2024",
        "Doanh thu 2024-03-31",
        "so sánh tháng 12/2024 và tháng 1/2025",
    ):
        assert impossible_date(fine) is None, fine


def test_shouting_and_acronyms_are_not_unknown_names() -> None:
    from app.conversation.intent import unfamiliar_name

    assert not unfamiliar_name("DOANH THU QUÝ TRƯỚC")
    assert not unfamiliar_name("Doanh thu năm 2024 dạng thẻ KPI")
    assert unfamiliar_name("Doanh thu Wakanda năm 2024")


def test_a_short_follow_up_can_be_read_without_a_model() -> None:
    from app.conversation.intent import follow_up_intent, merged_intent

    prior = {
        "slots": {
            "metric_id": "revenue", "dimension": "none", "period": "explicit",
            "start_date": "2025-01-01", "end_date": "2025-06-30", "factory_id": None,
            "territory": None, "limit": 100, "series_dimension": "none",
        }
    }  # fmt: skip
    raw = follow_up_intent("Canada thì sao")
    assert raw is not None and raw.territory == "Canada" and raw.metric_id is None
    merged = merged_intent(raw, prior, "Canada thì sao")
    assert (merged.metric_id, merged.territory, merged.period) == (
        "revenue",
        "Canada",
        "explicit",
    )
    by_area = merged_intent(follow_up_intent("theo khu vực"), prior, "theo khu vực")
    assert by_area.dimension == "sales_territory" and by_area.metric_id == "revenue"
    assert follow_up_intent("so với năm ngoái thì sao") is None


def test_everyday_words_for_last_period_are_understood() -> None:
    from app.core.dates import date_hints

    for text, period in (
        ("doanh thu năm ngoái bao nhiêu", "last_year"),
        ("doanh thu tháng vừa rồi", "last_month"),
        ("doanh thu quý vừa qua", "last_quarter"),
        ("doanh số tuần ngoái", "last_week"),
    ):
        assert date_hints(text).get("period") == period, text


def test_compare_with_a_named_year_uses_the_year_on_screen() -> None:
    from app.query.comparison import with_earlier_period

    slots = {"period": "explicit", "start_date": "2024-01-01", "end_date": "2025-01-01"}
    pair = with_earlier_period(named_periods("so sánh với 2023"), slots)
    assert [(k, s.year) for k, s, _ in pair] == [("year", 2023), ("year", 2024)]
    assert (
        with_earlier_period(named_periods("so sánh với tháng 3 năm 2023"), slots) == []
    )
    assert with_earlier_period(named_periods("so sánh với 2024"), slots) == []
    assert with_earlier_period(named_periods("so sánh với 2023"), {}) == []


def test_one_trailing_period_serves_every_request_before_it() -> None:
    from app.conversation.intent import split_tasks

    tasks = split_tasks("doanh thu Canada và sản lượng Factory A quý trước")
    assert tasks == ["doanh thu Canada quý trước", "sản lượng Factory A quý trước"]
    kept = split_tasks("doanh thu năm 2023 và sản lượng quý trước")
    assert kept == ["doanh thu năm 2023", "sản lượng quý trước"]


def test_a_bare_why_is_refused_and_thanks_work_mid_question() -> None:
    from app.conversation.dialogue import small_talk
    from app.conversation.intent import local_unsupported

    assert local_unsupported("tại sao lại như vậy?") is not None
    assert local_unsupported("tại sao doanh thu quý trước giảm") is None  # has a metric
    assert small_talk("cảm ơn", "vi", "manager", pending=True)
    assert small_talk("ok", "vi", "manager", pending=True) is None


def test_top_n_and_compare_with_follow_ups_read_without_a_model() -> None:
    from app.conversation.intent import follow_up_intent, merged_intent

    slots = {
        "metric_id": "revenue", "dimension": "sales_territory", "period": "explicit",
        "start_date": "2024-01-01", "end_date": "2025-01-01", "factory_id": None,
        "territory": None, "limit": 100, "series_dimension": "none",
    }  # fmt: skip
    prior = {"slots": slots}
    top = merged_intent(follow_up_intent("top 3", slots), prior, "top 3")
    assert (top.metric_id, top.dimension, top.limit) == (
        "revenue",
        "sales_territory",
        3,
    )
    only = merged_intent(
        follow_up_intent("chỉ France và Germany", slots), prior, "chỉ France và Germany"
    )
    assert only.territory == "France|Germany"
    assert (only.start_date, only.end_date) == ("2024-01-01", "2025-01-01")
    assert follow_up_intent("so sánh với 2023", slots) is not None
    assert follow_up_intent("so sánh với 2023", {}) is None
