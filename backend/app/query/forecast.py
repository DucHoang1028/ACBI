"""Forecast of a monthly series, computed here from recorded data and never by a model.

Two plain methods compete on a hold-out back-test: a straight-line trend and the
average of the last six months. The better one is refitted on all months and used,
unless there is too little history or even the better one misses the hold-out by
too much, in which case the request is refused instead of showing a weak number.
"""

import math
from dataclasses import dataclass
from datetime import date

from app.core.dates import month_start

METHOD_VERSION = 1
HOLDOUT = 6
Z95 = 1.96


class ForecastRefused(ValueError):
    """Why no forecast is offered; the reason is a short code plus the facts."""

    def __init__(self, reason: str, **facts: float | int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.facts = facts


@dataclass(frozen=True)
class Forecast:
    method: str
    history_months: int
    backtest_mape: float
    residual_std: float
    slope: float
    values: list[float]
    lower: list[float]
    upper: list[float]


def linear_fit(ys: list[float]) -> tuple[float, float]:
    """Least-squares intercept and slope against t = 0..n-1."""
    n = len(ys)
    mean_t = (n - 1) / 2
    mean_y = sum(ys) / n
    spread = sum((t - mean_t) ** 2 for t in range(n))
    slope = sum((t - mean_t) * (y - mean_y) for t, y in enumerate(ys)) / spread
    return mean_y - slope * mean_t, slope


def predict(method: str, ys: list[float], horizon: int) -> list[float]:
    n = len(ys)
    if method == "linear_trend":
        a, b = linear_fit(ys)
        return [a + b * (n + i) for i in range(horizon)]
    level = sum(ys[-HOLDOUT:]) / len(ys[-HOLDOUT:])
    return [level] * horizon


def mape(actual: list[float], predicted: list[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        raise ForecastRefused("zero_history")
    return sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def spread(method: str, ys: list[float]) -> float:
    """Typical monthly miss of the fitted method, used for the interval."""
    n = len(ys)
    if method == "linear_trend":
        a, b = linear_fit(ys)
        resid = [y - (a + b * t) for t, y in enumerate(ys)]
        return math.sqrt(sum(r * r for r in resid) / max(n - 2, 1))
    recent = ys[-12:]
    mean = sum(recent) / len(recent)
    return math.sqrt(sum((y - mean) ** 2 for y in recent) / max(len(recent) - 1, 1))


def make_forecast(
    ys: list[float], horizon: int, min_months: int, max_mape: float
) -> Forecast:
    if len(ys) < min_months:
        raise ForecastRefused("history", have=len(ys), need=min_months)
    scores = {
        method: mape(ys[-HOLDOUT:], predict(method, ys[:-HOLDOUT], HOLDOUT))
        for method in ("linear_trend", "recent_average")
    }
    method = min(scores, key=lambda name: scores[name])
    if scores[method] > max_mape:
        raise ForecastRefused("error", mape=scores[method], limit=max_mape)
    raw = predict(method, ys, horizon)
    sigma = spread(method, ys)
    slope = linear_fit(ys)[1] if method == "linear_trend" else 0.0
    floor = 0.0 if all(y >= 0 for y in ys) else -math.inf  # counts and sums stay >= 0
    band = [Z95 * sigma * math.sqrt(1 + (i + 1) / len(ys)) for i in range(horizon)]
    values = [max(floor, v) for v in raw]
    return Forecast(
        method=method,
        history_months=len(ys),
        backtest_mape=scores[method],
        residual_std=sigma,
        slope=slope,
        values=values,
        lower=[max(floor, v - w) for v, w in zip(raw, band)],
        upper=[max(p, v + w) for p, v, w in zip(values, raw, band)],
    )


def next_months(first: date, count: int) -> list[date]:
    return [month_start(first, i) for i in range(count)]


def contiguous(months: list[str]) -> bool:
    """True when the series has no missing month."""
    parsed = [date.fromisoformat(m[:10]) for m in months]
    return all(month_start(a, 1) == b for a, b in zip(parsed, parsed[1:]))
