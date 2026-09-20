"""Factual default summary and numeric guard for optional model summaries."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


def factual(metric_id: str, rows: list[dict[str, Any]], start: date, end: date) -> str:
    period = f"{start.isoformat()} to {(end - timedelta(days=1)).isoformat()}"
    label = metric_id.replace("_", " ").capitalize()
    if not rows:
        return f"No {label.lower()} records for {period}."
    if len(rows) == 1 and rows[0].get(metric_id) is not None:
        unit = (
            " source currency"
            if metric_id == "revenue"
            else " units" if metric_id == "production_output" else " ratio"
        )
        return f"{label}: {rows[0][metric_id]}{unit} for {period}."
    return f"{label}: {len(rows)} result rows for {period}. Values are in the table."


def numbers_match(summary: str, rows: list[dict[str, Any]]) -> bool:
    observed: list[Decimal] = []
    for row in rows:
        for value in row.values():
            try:
                if isinstance(value, bool):
                    continue
                observed.append(Decimal(str(value)))
            except InvalidOperation:
                pass
    numbers = re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])", summary)
    if not numbers:
        return True
    return all(
        any(
            abs(Decimal(value.replace(",", "")) - original) <= Decimal("0.000001")
            for original in observed
        )
        for value in numbers
    )
