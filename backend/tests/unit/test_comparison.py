from datetime import date

import pytest
from app.query.comparison import adjacent, label, named_periods, side_by_side


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
