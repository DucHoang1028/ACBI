# ruff: noqa: E501
"""Rolling-origin backtest of the app's ETS forecast against simple baselines.

    python scripts/backtest_forecast.py OUT.json   (needs the WAREHOUSE_* settings)


For every origin (history of at least MIN months) and horizon 1..6 months the forecast
is compared with what was recorded. Baselines: naive (last month), seasonal naive
(same month a year earlier), mean of the last 3 months."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from app.core.config import Settings
from app.query.forecast import ForecastRefused, make_forecast
from sqlalchemy import create_engine, text

OUT = Path(sys.argv[1])
MIN, H = 24, 6
engine = create_engine(Settings().warehouse_url())
QUERIES = {
    "revenue": "SELECT date_trunc('month',orderdate)::date m, SUM(subtotal) v FROM sales.salesorderheader WHERE orderdate < '2025-06-01' GROUP BY 1 ORDER BY 1",
    "production_output": "SELECT date_trunc('month',enddate)::date m, SUM(orderqty-scrappedqty) v FROM production.workorder WHERE enddate < '2025-06-01' GROUP BY 1 ORDER BY 1",
}
result = {}
for metric, q in QUERIES.items():
    with engine.connect() as c:
        rows = c.execute(text(q)).all()
    months = [str(r[0]) for r in rows]
    ys = [float(r[1]) for r in rows]
    cells = []  # one record per origin and horizon
    for t in range(MIN, len(ys) - 1):
        hist = ys[:t]
        for gated in (True, False):
            pass
        try:
            gated = make_forecast(hist, H, MIN, 0.35)
        except ForecastRefused as e:
            gated = None
            reason = e.reason
        ungated = make_forecast(hist, H, MIN, 1e9)
        for h in range(1, H + 1):
            if t + h - 1 >= len(ys):
                break
            actual = ys[t + h - 1]
            naive = hist[-1]
            seasonal = (
                hist[t - 12 + h - 1]
                if t - 12 + h - 1 < len(hist)
                else hist[-12 + (h - 1) % 12]
            )
            mean3 = sum(hist[-3:]) / 3
            cells.append(
                {
                    "origin": months[t - 1],
                    "h": h,
                    "actual": actual,
                    "ets": ungated.values[h - 1],
                    "lower": ungated.lower[h - 1],
                    "upper": ungated.upper[h - 1],
                    "refused": gated is None,
                    "naive": naive,
                    "seasonal": seasonal,
                    "mean3": mean3,
                    "method": ungated.method,
                }
            )
    result[metric] = {"months": months, "n_months": len(ys), "cells": cells}
    print(
        metric,
        "months",
        len(ys),
        "from",
        months[0],
        "to",
        months[-1],
        "cells",
        len(cells),
        flush=True,
    )
OUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
print("saved")
