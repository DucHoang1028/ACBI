"""Set one metric side by side across periods the user names (years, quarters, months).

Each period is an ordinary validated query; this module only finds the periods and
lays the results next to each other. Every figure in the text comes from the rows."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.dates import (
    Period,
    date_hints,
    minus_year,
    month_start,
    resolve_period,
    shifted_period,
)
from app.core.text import fold
from app.metadata import vocabulary
from app.presentation.analysis import amount, percent

MAX_PERIODS = 4
# Small breakdowns that can sit side by side across periods: dimension -> column.
GROUP_COLUMN = {
    "sales_territory": "territory",
    "factory": "factory",
    "product_category": "category",
    "production_line": "production_line",
    "scrap_reason": "reason",
}


def label(period: Period, language: str) -> str:
    kind, start, end = period
    vi = language == "vi"
    if kind == "year":
        return f"Năm {start.year}" if vi else f"Year {start.year}"
    if kind == "quarter":
        quarter = (start.month - 1) // 3 + 1
        return f"Quý {quarter}/{start.year}" if vi else f"Q{quarter} {start.year}"
    if kind == "range":
        return f"{start:%d/%m/%Y}–{end - timedelta(days=1):%d/%m/%Y}"
    return f"Tháng {start.month}/{start.year}" if vi else f"{start:%b %Y}"


def adjacent(periods: list[Period]) -> bool:
    """True when each period starts the day after the previous one ends."""
    return all(a[2] == b[1] for a, b in zip(periods, periods[1:]))


def kind_of(start: date, end: date) -> str:
    """ "year", "quarter" or "month" for a whole calendar unit, else "range"."""
    if start.day == 1:
        if start.month == 1 and end == date(start.year + 1, 1, 1):
            return "year"
        if start.month in (1, 4, 7, 10) and end == month_start(start, 3):
            return "quarter"
        if end == month_start(start, 1):
            return "month"
    return "range"


def _uniform(pair: list[Period]) -> list[Period]:
    """Two windows of different kinds are labelled as plain ranges."""
    if len({p[0] for p in pair}) > 1:
        return [("range", p[1], p[2]) for p in pair]
    return pair


def with_earlier_period(named: list[Period], slots: dict[str, Any]) -> list[Period]:
    """ "So sánh với 2023" after a 2024 answer: the period shown and the one named.

    Empty unless exactly one period is named and the earlier answer's period is
    explicit and of the same kind (a year against a year, a quarter against a quarter).
    """
    if len(named) != 1 or slots.get("period") != "explicit":
        return []
    try:
        start = date.fromisoformat(str(slots["start_date"]))
        end = date.fromisoformat(str(slots["end_date"]))
    except (KeyError, ValueError):
        return []
    if end == date(start.year + 1, 1, 1) and (start.month, start.day) == (1, 1):
        kind = "year"
    elif (
        start.day == 1 and start.month in (1, 4, 7, 10) and end == month_start(start, 3)
    ):
        kind = "quarter"
    elif start.day == 1 and end == month_start(start, 1):
        kind = "month"
    else:
        return []
    if kind != named[0][0] or named[0][1] == start:
        return []
    return sorted([named[0], (kind, start, end)], key=lambda p: p[1])


def shift_pair(question: str, slots: dict[str, Any], anchor: date) -> list[Period]:
    """ "Compare with the year before": the period on screen and the one it moves to."""
    moved = shifted_period(question, slots, anchor)
    if moved is None:
        return []
    try:
        if slots.get("period") == "explicit":
            start = date.fromisoformat(str(slots["start_date"]))
            end = date.fromisoformat(str(slots["end_date"]))
        else:
            start, end = resolve_period(str(slots["period"]), anchor)
    except (KeyError, ValueError):
        return []
    return _uniform(
        sorted(
            [
                (kind_of(*moved), moved[0], moved[1]),
                (kind_of(start, end), start, end),
            ],
            key=lambda p: p[1],
        )
    )


YEAR_OVER_YEAR = (
    r"\bso voi (?:cung ky )?nam (?:truoc|ngoai)\b|"
    r"\b(?:than|vs|versus|from|compared (?:to|with)) (?:the )?(?:previous|last) year\b|"
    r"\byear[- ]over[- ]year\b|\byoy\b"
)


def year_over_year(
    question: str,
    period: str | None,
    start_date: str | None,
    end_date: str | None,
    anchor: date,
) -> list[Period]:
    """ "...giảm so với năm trước": the window asked (last year unless stated) and the
    same window a year earlier."""
    if not re.search(YEAR_OVER_YEAR, fold(question)):
        return []
    try:
        if period == "explicit":
            start, end = date.fromisoformat(str(start_date)), date.fromisoformat(
                str(end_date)
            )
        else:
            start, end = resolve_period(period or "last_year", anchor)
    except ValueError:
        return []
    ago = (minus_year(start), minus_year(end))
    return _uniform(
        sorted(
            [(kind_of(*ago), *ago), (kind_of(start, end), start, end)],
            key=lambda p: p[1],
        )
    )


def relative_pair(question: str, slots: dict[str, Any], anchor: date) -> list[Period]:
    """ "So sánh với năm ngoái" after a this-year answer: the same stretch of both.

    The named period (last year, last quarter, last month) is set against the period
    on screen; when that one is still running, the named one is cut to the same length
    so that a part-year is not set against a whole year."""
    name = date_hints(question).get("period")
    if name not in {"last_year", "last_quarter", "last_month"}:
        return []
    try:
        if slots.get("period") == "explicit":
            start = date.fromisoformat(str(slots["start_date"]))
            end = date.fromisoformat(str(slots["end_date"]))
        else:
            start, end = resolve_period(str(slots["period"]), anchor)
    except (KeyError, ValueError):
        return []
    named_start, named_end = resolve_period(name, anchor)
    if (named_start, named_end) == (start, end):
        return []
    if named_end <= start and (named_end - named_start) > (end - start):
        # The named window lies before the one on screen and is longer (a whole last
        # year against this year so far): cut it to the same stretch.
        if name == "last_year":  # the same calendar stretch, not the same days
            try:
                same_stretch = end.replace(year=end.year - 1)
            except ValueError:  # 29 February
                same_stretch = end.replace(year=end.year - 1, day=28)
            named_end = min(named_end, same_stretch)
        else:
            named_end = min(named_end, named_start + (end - start))
    if named_end > start and named_start < end:  # overlap says nothing about change
        return []
    if named_end <= named_start:
        return []
    return _uniform(
        sorted(
            [
                (kind_of(named_start, named_end), named_start, named_end),
                (kind_of(start, end), start, end),
            ],
            key=lambda p: p[1],
        )
    )


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
    rank_by_change: bool = False,
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
    deltas = []
    for row in table:
        first, last = number(row.get(first_label)), number(row.get(last_label))
        if first is not None and last is not None:
            row["change_abs"] = str(last - first)
            deltas.append((str(row[by]), last - first))
        if first and last is not None:
            change = (last - first) / abs(first) * 100
            row["change_pct"] = f"{change:.1f}"
            moves.append((str(row[by]), change))
    if rank_by_change:  # "xếp hạng theo tăng trưởng": the table itself is the ranking
        table.sort(key=lambda r: -Decimal(r.get("change_pct") or "-1e9"))
    lead = table[0]
    lead_value = amount(number(lead.get(last_label)) or Decimal(0), metric, language)
    reading = (
        f" Đứng đầu {last_label}: {lead[by]} ({lead_value})."
        if vi
        else f" Top in {last_label}: {lead[by]} ({lead_value})."
    )
    if moves:
        top, bottom = max(moves, key=lambda m: m[1]), min(moves, key=lambda m: m[1])
        reading += (
            f" Tăng mạnh nhất: {top[0]} ({signed(top[1], language)}); "
            f"thấp nhất: {bottom[0]} ({signed(bottom[1], language)})."
            if vi
            else f" Biggest rise: {top[0]} ({signed(top[1], language)}); "
            f"weakest: {bottom[0]} ({signed(bottom[1], language)})."
        )
    fell = sorted((m for m in moves if m[1] < 0), key=lambda m: m[1])[:6]
    if moves:
        reading += (
            (
                " Giảm: "
                + ", ".join(f"{n} ({signed(v, language)})" for n, v in fell)
                + "."
                if fell
                else " Không nhóm nào giảm."
            )
            if vi
            else (
                " Fell: "
                + ", ".join(f"{n} ({signed(v, language)})" for n, v in fell)
                + "."
                if fell
                else " No group fell."
            )
        )
    total_delta = sum((d for _, d in deltas), Decimal(0))
    if deltas and total_delta != 0:
        pick = max if total_delta > 0 else min
        who_moved, moved_by = pick(deltas, key=lambda d: d[1])
        share = moved_by / total_delta * 100
        sign = "+" if total_delta > 0 else "-"
        figure = f"{sign}{amount(abs(total_delta), metric, language)}"
        part = (
            f"{'+' if moved_by >= 0 else '-'}{amount(abs(moved_by), metric, language)}"
        )
        reading += (
            f" Tổng thay đổi {figure}; đóng góp nhiều nhất: {who_moved} ({part}, "
            f"{percent(share, language)} tổng thay đổi)."
            if vi
            else f" Total change {figure}; largest contribution: {who_moved} ({part}, "
            f"{percent(share, language)} of the change)."
        )
    span = f"{first_label} – {last_label}"
    head = (
        f"{subject}, {len(table)} nhóm, {span}."
        if vi
        else (f"{subject}, {len(table)} groups, {span}.")
    )
    return table, f"{head}{reading}{gap}"
