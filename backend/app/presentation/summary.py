"""Factual default summary and numeric guard for optional model summaries."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.presentation.analysis import highlights


def factual(
    metric_id: str,
    rows: list[dict[str, Any]],
    start: date,
    end: date,
    language: str = "en",
) -> str:
    if language == "vi":
        label = {
            "revenue": "Doanh thu",
            "sales_growth": "Tăng trưởng doanh thu",
            "production_output": "Sản lượng",
            "defect_rate": "Tỷ lệ phế phẩm",
            "on_time_rate": "Tỷ lệ hoàn thành đúng hạn",
        }[metric_id]
        period_vi = f"{start:%d/%m/%Y} đến {end - timedelta(days=1):%d/%m/%Y}"
        if not rows:
            return f"Không có dữ liệu {label.lower()} từ {period_vi}."
        if len(rows) == 1 and rows[0].get(metric_id) is not None:
            number = Decimal(str(rows[0][metric_id]))
            ratio = metric_id in {"defect_rate", "sales_growth", "on_time_rate"}
            formatted = (
                f"{number * 100 if ratio else number:,.2f}".replace(",", "_")
                .replace(".", ",")
                .replace("_", ".")
            )
            unit = (
                "%"
                if ratio
                else " đơn vị tiền tệ nguồn" if metric_id == "revenue" else " sản phẩm"
            )
            return f"{label} từ {period_vi}: {formatted}{unit}."
        return f"{label} từ {period_vi}: {len(rows)} nhóm. " + (
            highlights(metric_id, rows, "vi") or "Chi tiết trong bảng bên dưới."
        )
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
    return f"{label} for {period}: {len(rows)} groups. " + (
        highlights(metric_id, rows, "en") or "Values are in the table."
    )


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


def share_text(
    rows: list[dict[str, Any]], names: list[str], start: date, end: date, language: str
) -> str | None:
    """Selected territories' share of total revenue, computed from result rows."""
    try:
        total = sum((Decimal(str(r["revenue"])) for r in rows), Decimal(0))
        picked = [r for r in rows if r.get("territory") in names]
        selected = sum((Decimal(str(r["revenue"])) for r in picked), Decimal(0))
    except (KeyError, ArithmeticError):
        return None
    if total <= 0 or not picked:
        return None
    percent = (selected / total * 100).quantize(Decimal("0.01"))
    label = ", ".join(str(r["territory"]) for r in picked)
    last = end - timedelta(days=1)
    if language == "vi":
        return (
            f"Doanh thu của {label} là {selected:,.2f}, chiếm {percent}% tổng doanh "
            f"thu {total:,.2f} của tất cả khu vực ({start} đến {last})."
        )
    return (
        f"Revenue for {label} is {selected:,.2f}, {percent}% of the {total:,.2f} "
        f"total across all territories ({start} to {last})."
    )
