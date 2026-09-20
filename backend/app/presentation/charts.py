"""Validate model-proposed mappings against real results, never model data."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class VizConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "bar", "line", "pie", "donut", "stacked_bar", "scatter", "table", "kpi_card"
    ]
    x: str | None
    y: list[str]
    series: str | None


TABLE = VizConfig(type="table", x=None, y=[], series=None)


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def describe(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns: dict[str, Any] = {}
    if not rows:
        return {"row_count": 0, "columns": columns}
    for key in rows[0]:
        values = [row.get(key) for row in rows]
        numeric = [number(v) for v in values]
        present = [n for n, v in zip(numeric, values) if v is not None]
        if present and all(n is not None for n in present):
            columns[key] = {
                "kind": "numeric",
                "nonnegative": all(n >= 0 for n in present if n is not None),
            }
        else:
            temporal = all(isinstance(v, str) and len(v) >= 10 for v in values)
            if temporal:
                try:
                    for value in values:
                        date.fromisoformat(str(value)[:10])
                except ValueError:
                    temporal = False
            columns[key] = {"kind": "temporal" if temporal else "category"}
        if columns[key]["kind"] == "numeric":
            numbers = [n for n in numeric if n is not None]
            columns[key]["ordered"] = numbers == sorted(numbers)
        else:
            strings = [str(v) for v in values]
            columns[key]["ordered"] = strings == sorted(strings)
        columns[key]["distinct"] = len({str(v) for v in values})
        columns[key]["complete"] = all(v is not None for v in values)
    return {"row_count": len(rows), "columns": columns}


def validate_viz(
    config: VizConfig, rows: list[dict[str, Any]], max_categories: int = 8
) -> VizConfig:
    if config.type == "table":
        return TABLE
    info = describe(rows)
    columns = info["columns"]
    selected = (
        config.y
        + ([config.x] if config.x else [])
        + ([config.series] if config.series else [])
    )
    if not rows or not selected or any(c not in columns for c in selected):
        raise ValueError("Use existing result columns")
    if len(selected) != len(set(selected)):
        raise ValueError("Mappings must use different columns")
    if not config.y or any(columns[c]["kind"] != "numeric" for c in config.y):
        raise ValueError("The value columns must be numeric and non-null")
    if config.type != "line" and any(not columns[c]["complete"] for c in config.y):
        raise ValueError("The value columns must be numeric and non-null")
    if config.type == "kpi_card":
        if len(rows) != 1 or len(config.y) != 1 or config.x or config.series:
            raise ValueError("KPI cards require one row and one numeric value")
        return config
    if not config.x or not columns[config.x]["complete"]:
        raise ValueError("An existing complete x column is required")
    if config.type in {"bar", "pie", "donut", "stacked_bar"} and columns[config.x][
        "kind"
    ] not in {"category", "temporal"}:
        raise ValueError("A categorical x column is required")
    if config.type == "line" and (
        columns[config.x]["kind"] not in {"temporal", "numeric"}
        or not columns[config.x]["ordered"]
    ):
        raise ValueError("Lines require ordered temporal or numeric x")
    if config.type in {"pie", "donut"}:
        if (
            len(config.y) != 1
            or len(rows) > max_categories
            or columns[config.x]["distinct"] != len(rows)
        ):
            raise ValueError("Pie charts require a small set of unique categories")
        if (
            not columns[config.y[0]]["nonnegative"]
            or sum(number(r[config.y[0]]) or 0 for r in rows) <= 0
        ):
            raise ValueError(
                "Pie charts require nonnegative values and a positive total"
            )
    if config.type == "scatter" and (
        len(config.y) != 1 or columns[config.x]["kind"] != "numeric"
    ):
        raise ValueError("Scatter charts require two numeric columns")
    if config.type == "stacked_bar":
        if (
            len(config.y) != 1
            or not config.series
            or columns[config.series]["kind"] != "category"
        ):
            raise ValueError(
                "Stacked bars require category, series and one numeric value"
            )
        pairs = {(str(r[config.x]), str(r[config.series])) for r in rows}
        if len(pairs) != len(rows):
            raise ValueError("Duplicate category/series pairs are ambiguous")
    elif config.series:
        raise ValueError("Series is only supported for stacked bars")
    if config.type in {"bar", "line"} and columns[config.x]["distinct"] != len(rows):
        raise ValueError("Repeated x values need explicit aggregation")
    return config
