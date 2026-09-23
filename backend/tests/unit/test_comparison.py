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
