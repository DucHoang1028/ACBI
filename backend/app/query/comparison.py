"""Set one metric side by side across periods the user names (years, quarters, months).

Each period is an ordinary validated query; this module only finds the periods and
lays the results next to each other. Every figure in the text comes from the rows."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.dates import month_start
from app.core.text import fold
from app.metadata import vocabulary
from app.presentation.analysis import amount, percent

MONTH_NAMES = (
    "january february march april may june july august september october november "
    "december"
).split()
EN_MONTHS = "|".join(MONTH_NAMES + [m[:3] for m in MONTH_NAMES] + ["sept"])
PERIOD = re.compile(
    r"\b(?:(?:quy|quarter)\s*(?P<q>[1-4])(?:\s*(?:nam|/|of|,))?\s*(?P<qy>20\d{2})"
    r"|thang\s*(?P<m>\d{1,2})(?:\s*(?:nam|/))?\s*(?P<my>20\d{2})"
    rf"|(?P<em>{EN_MONTHS})\.?\s*,?\s*(?P<ey>20\d{{2}})"
    r"|(?P<y>20\d{2}))\b"
)
MAX_PERIODS = 4
# Small breakdowns that can sit side by side across periods: dimension -> column.
GROUP_COLUMN = {
    "sales_territory": "territory",
    "factory": "factory",
    "product_category": "category",
    "production_line": "production_line",
    "scrap_reason": "reason",
}
Period = tuple[str, date, date]  # kind, first day, day after the last


def named_periods(question: str) -> list[Period]:
    """Every distinct period named in the question, oldest first."""
    value = re.sub(r"\bq([1-4])\b", r"quy \1", fold(question))
    found: dict[tuple[str, date], date] = {}
    for match in PERIOD.finditer(value):
        if match["q"]:
            kind, start = "quarter", date(int(match["qy"]), int(match["q"]) * 3 - 2, 1)
            end = month_start(start, 3)
        elif match["m"] or match["em"]:
            number = (
                int(match["m"])
                if match["m"]
                else next(
                    i
                    for i, name in enumerate(MONTH_NAMES, 1)
                    if name.startswith(match["em"][:3])
                )
            )
            if not 1 <= number <= 12:
                continue
            kind, start = "month", date(int(match["my"] or match["ey"]), number, 1)
            end = month_start(start, 1)
        else:
            kind, start = "year", date(int(match["y"]), 1, 1)
            end = date(start.year + 1, 1, 1)
        found[(kind, start)] = end
    return sorted(((k, s, e) for (k, s), e in found.items()), key=lambda p: p[1])


def label(period: Period, language: str) -> str:
    kind, start, _ = period
    vi = language == "vi"
    if kind == "year":
        return f"Năm {start.year}" if vi else f"Year {start.year}"
    if kind == "quarter":
        quarter = (start.month - 1) // 3 + 1
        return f"Quý {quarter}/{start.year}" if vi else f"Q{quarter} {start.year}"
    return f"Tháng {start.month}/{start.year}" if vi else f"{start:%b %Y}"


def adjacent(periods: list[Period]) -> bool:
    """True when each period starts the day after the previous one ends."""
    return all(a[2] == b[1] for a, b in zip(periods, periods[1:]))


def number(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except InvalidOperation:
        return None


def signed(value: Decimal, language: str) -> str:
    return ("+" if value >= 0 else "-") + percent(abs(value), language)


def change_text(
    metric: str, first: Decimal, last: Decimal, language: str
) -> str | None:
    """ "up 12.3%" for amounts, "up 1.2 percentage points" for ratios."""
    vi = language == "vi"
    if metric in vocabulary.get().ratio_metrics():
        moved, unit = (last - first) * 100, (
            "điểm phần trăm" if vi else "percentage points"
        )
        amount_text = f"{abs(moved):.1f} {unit}"
    elif first != 0:
        moved = (last - first) / abs(first) * 100
        amount_text = percent(abs(moved), language)
    else:
        return None
    word = ("tăng", "giảm") if vi else ("up", "down")
    return f"{word[0] if moved >= 0 else word[1]} {amount_text}"


def side_by_side(
    metric: str,
    results: list[tuple[str, list[dict[str, Any]]]],
    by: str | None,
    who: str,
    language: str,
) -> tuple[list[dict[str, Any]], str]:
    """The table and the plain reading for (period label, rows) pairs.

    Without `by` each period is one row. With `by` (a group column such as
    territory) each group is one row with one column per period."""
    vi = language == "vi"
    name = vocabulary.get().metric_label(metric, language)
    labels = [period for period, rows in results if rows]
    missing = [period for period, rows in results if not rows]
    gap = ""
    if missing:
        gap = (
            (" Không có dữ liệu cho: " if vi else " No data for: ")
            + ", ".join(missing)
            + "."
        )
    if len(labels) < 2:
        return [], (
            f"Chưa đủ dữ liệu để so sánh.{gap}"
            if vi
            else f"There is not enough data to compare.{gap}"
        )
    subject = f"{name} ({who})" if who else name
    first_label, last_label = labels[0], labels[-1]
    if by is None:
        table = [{"period": period, **rows[0]} for period, rows in results if rows]
        values = [number(row.get(metric)) for row in table]
        if None in values:
            return table, f"{subject}.{gap}"
        parts = "; ".join(
            f"{row['period']}: {amount(value, metric, language)}"
            for row, value in zip(table, values)
            if value is not None
        )
        moved = change_text(
            metric, values[0] or Decimal(0), values[-1] or Decimal(0), language
        )
        tail = ""
        if moved:
            tail = (
                f" {last_label} {moved} so với {first_label}."
                if vi
                else f" {last_label} is {moved} against {first_label}."
            )
        return table, f"{subject}: {parts}.{tail}{gap}"
    groups: dict[str, dict[str, Any]] = {}
    for period, rows in results:
        for row in rows:
            groups.setdefault(str(row[by]), {by: str(row[by])})[period] = row.get(
                metric
            )
    table = sorted(
        groups.values(), key=lambda r: -(number(r.get(last_label)) or Decimal(0))
    )
    moves = []
    for row in table:
        first, last = number(row.get(first_label)), number(row.get(last_label))
        if first and last is not None:
            moves.append((str(row[by]), (last - first) / abs(first) * 100))
    reading = ""
    if moves:
        top, bottom = max(moves, key=lambda m: m[1]), min(moves, key=lambda m: m[1])
        reading = (
            f" Tăng mạnh nhất: {top[0]} ({signed(top[1], language)}); "
            f"thấp nhất: {bottom[0]} ({signed(bottom[1], language)})."
            if vi
            else f" Biggest rise: {top[0]} ({signed(top[1], language)}); "
            f"weakest: {bottom[0]} ({signed(bottom[1], language)})."
        )
    span = f"{first_label} – {last_label}"
    head = (
        f"{subject}, {len(table)} nhóm, {span}."
        if vi
        else (f"{subject}, {len(table)} groups, {span}.")
    )
    return table, f"{head}{reading}{gap}"
