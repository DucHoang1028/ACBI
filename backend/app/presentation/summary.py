"""Factual default summary and numeric guard for optional model summaries."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.metadata import vocabulary
from app.presentation.analysis import highlights, money, percent


def named(row: dict[str, Any], metric_id: str) -> str:
    """The group a one-row result is about (a product, a territory), when it has one."""
    for key, value in row.items():
        if (
            isinstance(value, str)
            and key != metric_id
            and not key.lower().endswith("id")
            and not re.fullmatch(r"[\d.\-]+", value)
        ):
            return value
    return ""


def factual(
    metric_id: str,
    rows: list[dict[str, Any]],
    start: date,
    end: date,
    language: str = "en",
    scope: str = "",
) -> str:
    """The plain summary; `scope` names a territory or factory filter on many rows."""
    vocab = vocabulary.get()
    ratio = metric_id in vocab.ratio_metrics()
    metric = vocab.metrics.get(metric_id)
    unit = metric.unit if metric else ""
    if language == "vi":
        label = vocab.metric_label(metric_id, "vi")
        period_vi = f"{start:%d/%m/%Y} đến {end - timedelta(days=1):%d/%m/%Y}"
        if not rows:
            return f"Không có dữ liệu {label.lower()} từ {period_vi}."
        if len(rows) == 1 and rows[0].get(metric_id) is not None:
            number = Decimal(str(rows[0][metric_id]))
            formatted = (
                f"{number * 100 if ratio else number:,.2f}".replace(",", "_")
                .replace(".", ",")
                .replace("_", ".")
            )
            suffix = (
                "%"
                if ratio
                else (
                    " đơn vị tiền tệ nguồn"
                    if unit == "source_currency"
                    else " sản phẩm" if unit == "units" else ""
                )
            )
            who = named(rows[0], metric_id)
            return (
                f"{label}{f' của {who}' if who else ''} từ {period_vi}: "
                f"{formatted}{suffix}."
            )
        head = f"{label} ({scope})" if scope else label
        return f"{head} từ {period_vi}: {len(rows)} nhóm. " + (
            highlights(metric_id, rows, "vi") or "Chi tiết trong bảng bên dưới."
        )
    period = f"{start.isoformat()} to {(end - timedelta(days=1)).isoformat()}"
    label = vocab.metric_label(metric_id, "en")
    if not rows:
        return f"No {label.lower()} records for {period}."
    if len(rows) == 1 and rows[0].get(metric_id) is not None:
        suffix = (
            " source currency"
            if unit == "source_currency"
            else " units" if unit == "units" else " ratio" if ratio else ""
        )
        who = named(rows[0], metric_id)
        return (
            f"{label}{f' ({who})' if who else ''}: {rows[0][metric_id]}{suffix} "
            f"for {period}."
        )
    head = f"{label} ({scope})" if scope else label
    return f"{head} for {period}: {len(rows)} groups. " + (
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
    share = percent(selected / total * 100, language, 2)
    label = ", ".join(str(r["territory"]) for r in picked)
    last = end - timedelta(days=1)
    if language == "vi":
        return (
            f"Doanh thu của {label} là {money(selected, 'vi')}, chiếm {share} tổng "
            f"doanh thu {money(total, 'vi')} của tất cả khu vực "
            f"({start:%d/%m/%Y} đến {last:%d/%m/%Y})."
        )
    return (
        f"Revenue for {label} is {money(selected, 'en')}, {share} of the "
        f"{money(total, 'en')} total across all territories ({start} to {last})."
    )
