"""The forecast is arithmetic on recorded months; weak history is refused."""

import math
import random
from datetime import date

import pytest
from app.core.dates import resolve_period
from app.query.forecast import (
    Fit,
    ForecastRefused,
    Spec,
    contiguous,
    initial_states,
    make_forecast,
    next_months,
    run,
    select,
)

SEASONAL = [
    1000 + 300 * math.sin(2 * math.pi * t / 12) + 20 * t + (37 * t % 11) * 5
    for t in range(36)
]


def test_a_straight_line_is_extrapolated_exactly() -> None:
    history = [100.0 + 10 * t for t in range(30)]
    result = make_forecast(history, 3, 24, 0.35)
    assert result.method == "ETS(A,A,N)"  # Holt's linear trend
    assert result.values == pytest.approx([400.0, 410.0, 420.0])
    assert result.backtest_mape == pytest.approx(0.0, abs=1e-9)
    assert result.upper[0] == pytest.approx(result.lower[0], abs=1e-6)
    assert result.slope == pytest.approx(10.0)


def test_the_recursions_match_statsmodels() -> None:
    # statsmodels 0.15 ETSModel(error="add", trend="add", damped_trend=True,
    # seasonal="add", seasonal_periods=12, initialization_method="heuristic")
    # .smooth([0.3, 0.05, 0.1, 0.9]).forecast(3) on the same series.
    spec = Spec("Ad", "A")
    params = {"alpha": 0.3, "beta": 0.05, "gamma": 0.1, "phi": 0.9}
    sse, level, trend, seasons = run(SEASONAL, params, initial_states(SEASONAL, spec))
    fitted = Fit(spec, params, sse, 36, level, trend, tuple(seasons))
    assert fitted.point(3) == pytest.approx(
        [1716.184458, 1874.899065, 1992.866083], abs=1e-5
    )


def test_a_clear_yearly_season_is_modelled() -> None:
    assert select(SEASONAL).spec.season == "A"
    result = make_forecast(SEASONAL, 12, 24, 0.35)
    assert result.method.endswith(",A)")
    peak, trough = result.values.index(max(result.values)), result.values.index(
        min(result.values)
    )
    assert abs(peak - trough) == 6  # the season repeats: high and low half a year apart
    widths = [u - lo for u, lo in zip(result.upper, result.lower)]
    assert widths == sorted(widths)  # uncertainty grows with the horizon


def test_short_or_erratic_history_is_refused() -> None:
    with pytest.raises(ForecastRefused) as short:
        make_forecast([1.0] * 10, 3, 24, 0.35)
    assert short.value.reason == "history"
    noise = random.Random(7)
    erratic = [noise.uniform(10, 1000) for _ in range(30)]
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
