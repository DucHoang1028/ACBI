"""The forecast is arithmetic on recorded months; weak history is refused."""

from datetime import date

import pytest
from app.core.dates import resolve_period
from app.query.forecast import (
    ForecastRefused,
    contiguous,
    make_forecast,
    next_months,
)


def test_a_straight_line_is_extrapolated_exactly() -> None:
    history = [100.0 + 10 * t for t in range(30)]
    result = make_forecast(history, 3, 24, 0.35)
    assert result.method == "linear_trend"
    assert result.values == pytest.approx([400.0, 410.0, 420.0])
    assert result.backtest_mape == pytest.approx(0.0, abs=1e-9)
    assert result.upper[0] == pytest.approx(result.lower[0], abs=1e-6)


def test_short_or_erratic_history_is_refused() -> None:
    with pytest.raises(ForecastRefused) as short:
        make_forecast([1.0] * 10, 3, 24, 0.35)
    assert short.value.reason == "history"
    erratic = [100.0, 900.0] * 15
    with pytest.raises(ForecastRefused) as wild:
        make_forecast(erratic, 3, 24, 0.35)
    assert wild.value.reason == "error"
    with pytest.raises(ForecastRefused):
        make_forecast([0.0] * 30, 3, 24, 0.35)


def test_a_falling_series_never_forecasts_below_zero() -> None:
    history = [max(0.0, 300.0 - 10 * t) for t in range(30)]
    result = make_forecast(history, 6, 24, 1.0)
    assert min(result.values) >= 0 and min(result.lower) >= 0
    assert all(u >= v for u, v in zip(result.upper, result.values))


def test_month_helpers() -> None:
    assert contiguous(["2025-01-01", "2025-02-01", "2025-03-01"])
    assert not contiguous(["2025-01-01", "2025-03-01"])
    assert next_months(date(2025, 11, 1), 3) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_day_and_week_periods_follow_the_data_date() -> None:
    anchor = date(2025, 6, 29)  # a Sunday
    assert resolve_period("today", anchor) == (date(2025, 6, 29), date(2025, 6, 30))
    assert resolve_period("yesterday", anchor) == (date(2025, 6, 28), date(2025, 6, 29))
    assert resolve_period("this_week", anchor) == (date(2025, 6, 23), date(2025, 6, 30))
    assert resolve_period("last_week", anchor) == (date(2025, 6, 16), date(2025, 6, 23))


def test_a_follow_up_that_names_no_period_is_about_the_answer_just_given() -> None:
    from app.core.dates import mentions_time

    for asking_about_it in (
        "Đây là dự báo dựa trên doanh thu tổng à",
        "Con số đó chắc chắn không?",
        "Is that a forecast?",
    ):
        assert not mentions_time(asking_about_it), asking_about_it
    for a_new_request in (
        "Dự báo doanh thu 6 tháng tới",
        "Dự báo sản lượng quý tới",
        "Forecast revenue for next year",
        "Doanh thu tháng này",
        "Dự báo đến 2027",
    ):
        assert mentions_time(a_new_request), a_new_request
