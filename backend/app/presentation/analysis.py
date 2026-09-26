"""Plain-language reading of a result: comparisons, extremes, totals and trends.

Every figure is computed from the rows the query returned, never supplied by a
model, so a sentence here can always be checked against the table beside it.
"""

import calendar
import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.dates import GROWTH, date_hints
from app.core.text import fold
from app.metadata import vocabulary
from app.presentation.charts import describe, number

HIDDEN = {"sample_count", "ordered_units", "scrapped_units"}
ANCHOR: date | None = None  # the data's last day, set at startup

WHICH = r"\b(?:cai nao|ben nao|nuoc nao|khu vuc nao|thang nao|nam nao|nhom nao|which)\b"
KINDS = {
    "difference": (
        r"\b(?:chenh lech|khac nhau|hon nhau|difference|gap)\b|hon bao nhieu|"
        r"\b(?:cao|nhieu|lon|thap|it) hon\b.*\b(?:phan tram|percent)\b|"
        r"\b(?:higher|lower|more|less)\b.*\bpercent\b"
    ),
    "share": r"\b(?:chiem bao nhieu|phan tram|ty trong|share|percent)\b",
    "total": r"\b(?:tong|cong lai|total|sum)\b",
    "average": r"\b(?:trung binh|average|mean)\b",
    "lower": r"\b(?:it hon|thap hon|nho hon|yeu hon|lower|less|smaller)\b",
    "higher": r"\b(?:nhieu hon|cao hon|lon hon|manh hon|higher|more|larger|bigger)\b",
    "min": r"\b(?:thap nhat|it nhat|nho nhat|lowest|least|smallest)\b",
    "max": (
        r"\b(?:cao nhat|nhieu nhat|lon nhat|dung dau|highest|most|largest|biggest)\b"
    ),
}


def analysis_kind(question: str) -> str | None:
    """The comparison a user asks about a result already on screen, if any."""
    value = fold(question)
    for kind, pattern in KINDS.items():
        if re.search(pattern, value):
            return kind
    return "higher" if re.search(WHICH, value) else None


RATIO = r"\b(?:bao nhieu lan|gap may lan|gap bao nhieu|how many times|times as)\b"


def ratio_text(
    rows: list[dict[str, Any]], metric: str, question: str, language: str
) -> str | None:
    """ "Bikes so với Accessories là bao nhiêu lần": the first named over the second."""
    value = fold(question)
    if not re.search(RATIO, value):
        return None
    cols = columns(rows, metric)
    if cols is None:
        return None
    label, number_col = cols
    at = []
    for row in rows:
        name = str(row[label])
        found = re.search(r"(?<![\w])" + re.escape(fold(name)) + r"(?![\w])", value)
        if found and (n := number(row.get(number_col))) is not None:
            at.append((found.start(), name, n))
    at.sort()
    if len(at) != 2 or at[1][2] == 0:
        return None
    (_, first, a), (_, second, b) = at
    times = f"{a / b:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if language != "vi":
        times = f"{a / b:,.1f}"
    return (
        f"{first} gấp {times} lần {second} ({amount(a, metric, language)} so với "
        f"{amount(b, metric, language)})."
        if language == "vi"
        else f"{first} is {times} times {second} ({amount(a, metric, language)} vs "
        f"{amount(b, metric, language)})."
    )


def names_new_member(question: str, rows: list[dict[str, Any]]) -> bool:
    """ "So với Úc thì ai cao hơn?" names a member the result on screen lacks."""
    shown = {fold(str(v)) for row in rows for v in row.values()}
    for names in vocabulary.get().match_members(fold(question)).values():
        if any(fold(name) not in shown for name in names):
            return True
    return False


def fresh_request(question: str, rows: list[dict[str, Any]], metric: str) -> bool:
    """True when a "follow-up" really asks for new figures, not a reading of the table.

    It names a period of its own, a member the table lacks, or a unit the table is
    not split by."""
    value = fold(question)
    return bool(
        names_new_member(question, rows)
        or asks_other_unit(question, rows, metric)
        or date_hints(question)
        or re.search(GROWTH, value)  # growth is not readable from a table on screen
        or re.search(r"\d", value)
    )


def asks_other_unit(question: str, rows: list[dict[str, Any]], metric: str) -> bool:
    """Naming a breakdown the table on screen is not split by is a new question.

    "Tháng nào cao nhất?" over a table of territories, or "khu vực thấp nhất" over
    a table of months, asks for other figures than the ones shown."""
    cols = columns(rows, metric)
    if cols is None:
        return False
    label = cols[0].lower()
    named = vocabulary.get().match_dimensions(fold(question))
    return bool(named) and not any(label in d or d in label for d in named)


def columns(rows: list[dict[str, Any]], metric: str) -> tuple[str, str] | None:
    """(label column, value column) of a breakdown result."""
    if not rows:
        return None
    info = describe(rows)["columns"]
    shown = [c for c in rows[0] if not c.lower().endswith("id")]
    label = next(
        (c for c in shown if info[c]["kind"] in {"category", "temporal"}), None
    )
    numeric = [c for c in shown if info[c]["kind"] == "numeric" and c not in HIDDEN]
    value = metric if metric in numeric else (numeric[0] if numeric else None)
    return (label, value) if label and value else None


def money(value: Decimal, language: str, decimals: int = 2) -> str:
    text = f"{value:,.{decimals}f}"
    if language == "vi":
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def amount(value: Decimal, metric: str, language: str) -> str:
    if metric in vocabulary.get().ratio_metrics():
        return percent(value * 100, language)
    return money(value, language)


def percent(value: Decimal, language: str, decimals: int = 1) -> str:
    text = f"{value:.{decimals}f}"
    return (text.replace(".", ",") if language == "vi" else text) + "%"


def label_text(column: str, value: Any, language: str) -> str:
    """A friendly name for a group; month and day values read as dates."""
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = text.split("-")
        if column == "month":
            return f"tháng {month}/{year}" if language == "vi" else f"{year}-{month}"
        return f"{day}/{month}/{year}" if language == "vi" else text
    return text


def ranked(
    rows: list[dict[str, Any]], label: str, value: str
) -> list[tuple[str, Decimal]]:
    pairs = [
        (str(r[label]), n) for r in rows if (n := number(r.get(value))) is not None
    ]
    return sorted(pairs, key=lambda pair: pair[1], reverse=True)


def relative(bigger: Decimal, smaller: Decimal, language: str) -> str:
    if smaller == 0:
        return ""
    gap = (bigger - smaller) / abs(smaller) * 100
    return percent(gap, language)


def analyze(
    kind: str, rows: list[dict[str, Any]], metric: str, language: str
) -> str | None:
    """One grounded sentence or two about the rows, or None when they cannot answer."""
    vi = language == "vi"
    cols = columns(rows, metric)
    if cols is None:
        return None
    label, value = cols
    pairs = ranked(rows, label, value)
    name = vocabulary.get().metric_label(metric, language).lower()
    if len(pairs) < 2:
        return (
            "Kết quả trước chỉ có một giá trị nên chưa có gì để so sánh."
            if vi
            else "The previous result has one value, so there is nothing to compare."
        )

    def show(pair: tuple[str, Decimal]) -> str:
        return (
            f"{label_text(label, pair[0], language)} "
            f"({amount(pair[1], metric, language)})"
        )

    top, bottom = pairs[0], pairs[-1]
    if kind in {"higher", "lower", "max", "min"}:
        winner, other = (top, bottom) if kind in {"higher", "max"} else (bottom, top)
        if len(pairs) == 2:
            gap = abs(top[1] - bottom[1])
            up = kind in {"higher", "max"}
            if top[1] == bottom[1]:
                return (
                    f"Hai giá trị bằng nhau: {show(top)} và {show(bottom)}."
                    if vi
                    else f"The two values are equal: {show(top)} and {show(bottom)}."
                )
            rel = relative(top[1], bottom[1], language)
            gap_text = amount(gap, metric, language)
            tail = f", chênh {gap_text}" + (f" (+{rel})" if rel else "")
            en_tail = f", a gap of {gap_text}" + (f" (+{rel})" if rel else "")
            word_vi = "cao hơn" if up else "thấp hơn"
            word_en = "higher" if up else "lower"
            who, versus = (top, bottom) if up else (bottom, top)  # winner first
            return (
                f"{label_text(label, who[0], language)} {word_vi} "
                f"{label_text(label, versus[0], language)}: "
                f"{amount(who[1], metric, language)} so với "
                f"{amount(versus[1], metric, language)}{tail}."
                if vi
                else f"{label_text(label, who[0], language)} is {word_en} than "
                f"{label_text(label, versus[0], language)}: "
                f"{amount(who[1], metric, language)} vs "
                f"{amount(versus[1], metric, language)}{en_tail}."
            )
        runner = pairs[1] if kind in {"higher", "max"} else pairs[-2]
        extreme = "cao nhất" if kind in {"higher", "max"} else "thấp nhất"
        extreme_en = "highest" if kind in {"higher", "max"} else "lowest"
        return (
            f"{name.capitalize()} {extreme} là {show(winner)}, tiếp theo là "
            f"{show(runner)}; ngược lại là {show(other)}."
            if vi
            else f"{name.capitalize()} is {extreme_en} for {show(winner)}, next "
            f"{show(runner)}; the opposite end is {show(other)}."
        )
    if kind == "difference":
        gap = top[1] - bottom[1]
        rel = relative(top[1], bottom[1], language)
        return (
            f"Chênh lệch giữa {show(top)} và {show(bottom)} là "
            f"{amount(gap, metric, language)}"
            + (f", tức {rel} so với giá trị thấp hơn." if rel else ".")
            if vi
            else f"The gap between {show(top)} and {show(bottom)} is "
            f"{amount(gap, metric, language)}"
            + (f", {rel} above the lower value." if rel else ".")
        )
    if metric in vocabulary.get().ratio_metrics():
        return (
            f"{name.capitalize()} là một tỷ lệ nên không cộng dồn hay chia phần "
            f"được giữa các nhóm; cao nhất là {show(top)}, thấp nhất là {show(bottom)}."
            if vi
            else f"{name.capitalize()} is a ratio, so it cannot be added or split "
            f"across groups; highest is {show(top)}, lowest is {show(bottom)}."
        )
    total = sum((p[1] for p in pairs), Decimal(0))
    if kind == "total":
        return (
            f"Tổng {name} của {len(pairs)} nhóm là {money(total, language)}."
            if vi
            else f"Total {name} across {len(pairs)} groups: {money(total, language)}."
        )
    if kind == "average":
        mean = total / len(pairs)
        return (
            f"{name.capitalize()} trung bình mỗi nhóm là {money(mean, language)} "
            f"(tổng {money(total, language)} chia cho {len(pairs)} nhóm)."
            if vi
            else f"Average {name} per group is {money(mean, language)} "
            f"({money(total, language)} over {len(pairs)} groups)."
        )
    if total <= 0:
        return None
    shares = [
        f"{label_text(label, k, language)} {percent(v / total * 100, language)}"
        for k, v in pairs[:6]
    ]
    rest = (
        (
            f" và {len(pairs) - 6} nhóm còn lại"
            if vi
            else f" and {len(pairs) - 6} more groups"
        )
        if len(pairs) > 6
        else ""
    )
    return (
        f"Tỷ trọng trong tổng {name} ({money(total, language)}): "
        + ", ".join(shares)
        + rest
        + "."
        if vi
        else f"Share of total {name} ({money(total, language)}): "
        + ", ".join(shares)
        + rest
        + "."
    )


def highlights(metric: str, rows: list[dict[str, Any]], language: str) -> str | None:
    """Two short observations for a breakdown: extremes, then spread or trend."""
    vi = language == "vi"
    cols = columns(rows, metric)
    if cols is None or len(rows) < 2:
        return None
    label, value = cols
    pairs = ranked(rows, label, value)
    if len(pairs) < 2:
        return None
    name = vocabulary.get().metric_label(metric, language).lower()
    top, bottom = pairs[0], pairs[-1]

    def show(pair: tuple[str, Decimal]) -> str:
        return (
            f"{label_text(label, pair[0], language)} "
            f"({amount(pair[1], metric, language)})"
        )

    if len(pairs) == 2:
        rel = relative(top[1], bottom[1], language)
        return (
            f"{label_text(label, top[0], language)} cao hơn "
            f"{label_text(label, bottom[0], language)} "
            f"{amount(top[1] - bottom[1], metric, language)}"
            + (f" (+{rel})." if rel else ".")
            if vi
            else f"{label_text(label, top[0], language)} is above "
            f"{label_text(label, bottom[0], language)} by "
            f"{amount(top[1] - bottom[1], metric, language)}"
            + (f" (+{rel})." if rel else ".")
        )
    text = (
        f"Cao nhất là {show(top)}, thấp nhất là {show(bottom)}."
        if vi
        else f"Highest is {show(top)}, lowest is {show(bottom)}."
    )
    temporal = describe(rows)["columns"][label]["kind"] == "temporal"
    if temporal:
        ordered = [
            (str(r[label]), n) for r in rows if (n := number(r.get(value))) is not None
        ]
        unfinished = ""
        if ANCHOR and label == "month" and len(ordered) > 2:
            end_month = date.fromisoformat(ordered[-1][0][:10])
            last_day = calendar.monthrange(end_month.year, end_month.month)[1]
            if (end_month.year, end_month.month) == (ANCHOR.year, ANCHOR.month) and (
                ANCHOR.day < last_day
            ):
                # The data stops mid-month: that month is not a point on the trend.
                unfinished = label_text(label, ordered[-1][0], language)
                ordered = ordered[:-1]
        first, last = ordered[0], ordered[-1]
        if unfinished:
            text += (
                f" ({unfinished} chưa đủ tháng nên không tính vào xu hướng.)"
                if vi
                else f" ({unfinished} is not a full month, so it is not in the trend.)"
            )
        if first[1] != 0:
            change = (last[1] - first[1]) / abs(first[1]) * 100
            word = ("tăng", "increased") if change >= 0 else ("giảm", "decreased")
            text += (
                f" Từ {label_text(label, first[0], language)} đến "
                f"{label_text(label, last[0], language)}, {name} {word[0]} "
                f"{percent(abs(change), language)}."
                if vi
                else f" From {label_text(label, first[0], language)} to "
                f"{label_text(label, last[0], language)}, {name} {word[1]} "
                f"{percent(abs(change), language)}."
            )
    elif metric not in vocabulary.get().ratio_metrics():
        total = sum((p[1] for p in pairs), Decimal(0))
        if total > 0:
            text += (
                f" {label_text(label, top[0], language)} chiếm "
                f"{percent(top[1] / total * 100, language)} trong tổng "
                f"{money(total, language)}."
                if vi
                else f" {label_text(label, top[0], language)} makes up "
                f"{percent(top[1] / total * 100, language)} of the "
                f"{money(total, language)} total."
            )
    return text
