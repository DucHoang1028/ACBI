"""Forecast of a monthly series, computed here from recorded data and never by a model.

Exponential smoothing in state-space form (ETS; Hyndman, Koehler, Ord & Snyder 2008),
the method behind R's forecast::ets and statsmodels' ETSModel: additive-error models
with no, linear or damped trend, each with or without a 12-month season. Smoothing
parameters minimise the squared one-step errors, initial states use the heuristic of
Hyndman et al. (section 2.6, as statsmodels does), and the model with the lowest AICc
is used. Intervals are the analytical ones for these linear models.

The whole procedure is also run without the last six months and scored on them: when
it misses them by too much, or the history is too short, the request is refused
instead of showing a weak number.
"""

import math
from dataclasses import dataclass
from datetime import date
from itertools import product

from app.core.dates import month_start

METHOD_VERSION = 2
HOLDOUT = 6
SEASON = 12
Z95 = 1.959964
STARTS = {
    "alpha": (0.1, 0.3, 0.5, 0.7, 0.9),
    "beta": (0.01, 0.05, 0.15),
    "gamma": (0.01, 0.1, 0.3),
    "phi": (0.85, 0.95),
}
STEPS = (0.05, 0.01, 0.002)


class ForecastRefused(ValueError):
    """Why no forecast is offered; the reason is a short code plus the facts."""

    def __init__(self, reason: str, **facts: float | int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.facts = facts


@dataclass(frozen=True)
class Spec:
    trend: str  # "N" none, "A" additive, "Ad" additive damped
    season: str  # "N" none, "A" additive

    @property
    def name(self) -> str:
        return f"ETS(A,{self.trend},{self.season})"

    @property
    def free(self) -> tuple[str, ...]:
        names = ["alpha"]
        if self.trend != "N":
            names.append("beta")
        if self.season == "A":
            names.append("gamma")
        if self.trend == "Ad":
            names.append("phi")
        return tuple(names)

    @property
    def k(self) -> int:
        """Estimated quantities as R's ets counts them: parameters, states, sigma."""
        states = 1 + (self.trend != "N") + (SEASON - 1) * (self.season == "A")
        return len(self.free) + states + 1


SPECS = tuple(Spec(t, s) for s in ("N", "A") for t in ("N", "A", "Ad"))


@dataclass(frozen=True)
class Fit:
    spec: Spec
    params: dict[str, float]
    sse: float
    n: int
    level: float
    trend: float
    seasons: tuple[float, ...]  # seasons[i]: effect i + 1 months after the data

    @property
    def aicc(self) -> float:
        k = self.spec.k
        return (
            self.n * math.log(max(self.sse, 1e-12) / self.n)
            + 2 * k
            + 2 * k * (k + 1) / (self.n - k - 1)
        )

    def point(self, horizon: int) -> list[float]:
        phi = self.params.get("phi", 1.0)
        damp, out = 0.0, []
        for h in range(1, horizon + 1):
            damp += phi**h
            out.append(self.level + damp * self.trend + self.seasons[(h - 1) % SEASON])
        return out

    def sigma(self) -> float:
        return math.sqrt(self.sse / max(self.n - self.spec.k, 1))

    def widths(self, horizon: int) -> list[float]:
        """95% half-widths: sigma^2 (1 + sum of c_j^2), Hyndman et al. (2008) ch. 6."""
        alpha = self.params["alpha"]
        beta = self.params.get("beta", 0.0)
        gamma = self.params.get("gamma", 0.0)
        phi = self.params.get("phi", 1.0)
        out, total, damp = [], 0.0, 0.0
        for h in range(1, horizon + 1):
            out.append(Z95 * self.sigma() * math.sqrt(1 + total))
            damp += phi**h
            c = alpha + beta * damp + (gamma if h % SEASON == 0 else 0.0)
            total += c * c
        return out


def line(ys: list[float]) -> tuple[float, float]:
    """Least-squares intercept and slope of ys against t = 1..n."""
    n = len(ys)
    mean_t, mean_y = (n + 1) / 2, sum(ys) / n
    slope = sum((t - mean_t) * (y - mean_y) for t, y in enumerate(ys, 1)) / sum(
        (t - mean_t) ** 2 for t in range(1, n + 1)
    )
    return mean_y - slope * mean_t, slope


def initial_states(ys: list[float], spec: Spec) -> tuple[float, float, list[float]]:
    """Level, trend and seasons from Hyndman et al. (2008), section 2.6.1."""
    seasons = [0.0] * SEASON
    base = ys
    if spec.season == "A":
        half = SEASON // 2
        cycles = max(min(5, len(ys) // SEASON), math.ceil((10 + 2 * half) / SEASON))
        window = ys[: SEASON * cycles]
        # Centred 2x12 moving average, then the mean detrended value of each month.
        average = {
            t: (
                0.5 * window[t - half]
                + sum(window[t - half + 1 : t + half])
                + 0.5 * window[t + half]
            )
            / SEASON
            for t in range(half, len(window) - half)
        }
        effects = [
            [
                window[t] - average[t]
                for t in range(i, len(window), SEASON)
                if t in average
            ]
            for i in range(SEASON)
        ]
        seasons = [sum(e) / len(e) for e in effects]
        mean = sum(seasons) / SEASON
        seasons = [s - mean for s in seasons]
        base = [average[t] for t in sorted(average)]
    level, slope = line(base[:10])
    return level, slope if spec.trend != "N" else 0.0, seasons


def run(
    ys: list[float],
    params: dict[str, float],
    start: tuple[float, float, list[float]],
) -> tuple[float, float, float, list[float]]:
    """One-step errors through the data: their sum of squares and the final states.

    A parameter a model lacks (beta, gamma, phi) leaves that component at rest."""
    alpha = params["alpha"]
    beta = params.get("beta", 0.0)
    gamma = params.get("gamma", 0.0)
    phi = params.get("phi", 1.0)
    level, trend, seasons = start[0], start[1], list(start[2])
    sse = 0.0
    for y in ys:
        step = phi * trend
        error = y - (level + step + seasons[0])
        sse += error * error
        level += step + alpha * error
        trend = step + beta * error
        seasons = seasons[1:] + [seasons[0] + gamma * error]
    return sse, level, trend, seasons


def valid(p: dict[str, float]) -> bool:
    """R's usual bounds: 0 < beta < alpha < 1, 0 < gamma < 1 - alpha, phi 0.8-0.98."""
    alpha = p["alpha"]
    return (
        1e-4 <= alpha <= 0.9999
        and 1e-4 <= p.get("beta", 1e-4) <= alpha
        and 1e-4 <= p.get("gamma", 1e-4) <= 1 - alpha
        and 0.8 <= p.get("phi", 0.9) <= 0.98
    )


def fit(ys: list[float], spec: Spec) -> Fit:
    """Least squares over the smoothing parameters: grid start, then pattern search."""
    start = initial_states(ys, spec)
    names = spec.free

    def cost(values: tuple[float, ...]) -> float:
        p = dict(zip(names, values))
        return run(ys, p, start)[0] if valid(p) else math.inf

    best = min(product(*(STARTS[n] for n in names)), key=cost)
    best_cost = cost(best)
    for step in STEPS:
        moved = True
        while moved:
            moved = False
            for i, sign in product(range(len(names)), (1, -1)):
                trial = best[:i] + (best[i] + sign * step,) + best[i + 1 :]
                if (trial_cost := cost(trial)) < best_cost:
                    best, best_cost, moved = trial, trial_cost, True
    params = dict(zip(names, best))
    sse, level, trend, seasons = run(ys, params, start)
    return Fit(spec, params, sse, len(ys), level, trend, tuple(seasons))


def select(ys: list[float]) -> Fit:
    """The model with the lowest AICc among those the data can support."""
    fits = [
        fit(ys, spec)
        for spec in SPECS
        if len(ys) > spec.k + 4  # R's ets: no model with nearly as many quantities
        and (spec.season == "N" or len(ys) >= 2 * SEASON)
    ]
    return min(fits, key=lambda f: f.aicc)


def mape(actual: list[float], predicted: list[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        raise ForecastRefused("zero_history")
    return sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


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


def make_forecast(
    ys: list[float], horizon: int, min_months: int, max_mape: float
) -> Forecast:
    if len(ys) < min_months:
        raise ForecastRefused("history", have=len(ys), need=min_months)
    if not any(ys):
        raise ForecastRefused("zero_history")
    held = select(ys[:-HOLDOUT])
    score = mape(ys[-HOLDOUT:], held.point(HOLDOUT))
    if score > max_mape:
        raise ForecastRefused("error", mape=score, limit=max_mape)
    chosen = select(ys)
    raw = chosen.point(horizon)
    band = chosen.widths(horizon)
    floor = 0.0 if all(y >= 0 for y in ys) else -math.inf  # counts and sums stay >= 0
    values = [max(floor, v) for v in raw]
    return Forecast(
        method=chosen.spec.name,
        history_months=len(ys),
        backtest_mape=score,
        residual_std=chosen.sigma(),
        slope=chosen.params.get("phi", 1.0) * chosen.trend,
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
