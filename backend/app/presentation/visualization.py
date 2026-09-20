"""Result presentation: user-named chart types, column mapping and AI proposals."""

import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.ai.budget import RequestBudget
from app.ai.client import FakeLLM
from app.core.dates import METRIC_WORDS, fold
from app.presentation.charts import TABLE, VizConfig, describe, validate_viz

CHART_WORDS = (
    r"\b(?:bieu do|chart|kpi|the chi so|kieu|as a|table|stacked|scatter|"
    r"dang (?:bieu do|bang|cot|duong|tron|vanh|xep|diem))\b"
)


CHART_KINDS = {
    "stacked_bar": r"\b(?:xep chong|cot chong|stacked)\b",
    "donut": r"\b(?:donut|vanh khuyen|hinh vanh)\b",
    "pie": r"\b(?:tron|pie)\b",
    "scatter": r"\b(?:phan tan|scatter|diem)\b",
    "kpi_card": r"\b(?:kpi|the chi so)\b",
    "line": r"\b(?:duong|line chart|line graph)\b",
    "bar": r"\b(?:cot|bar)\b",
    "table": r"\b(?:dang bang|bang du lieu|as a table|table)\b",
}


KIND_NAMES = {
    "stacked_bar": ("biểu đồ cột chồng", "stacked bar chart"),
    "scatter": ("biểu đồ phân tán", "scatter plot"),
    "kpi_card": ("thẻ KPI", "KPI card"),
    "pie": ("biểu đồ tròn", "pie chart"),
    "donut": ("biểu đồ vành khuyên", "donut chart"),
    "line": ("biểu đồ đường", "line chart"),
    "bar": ("biểu đồ cột", "bar chart"),
    "table": ("bảng", "table"),
}


def requested_chart(question: str) -> str | None:
    """The chart type the user explicitly asked for, if any."""
    value = fold(question)
    if not re.search(CHART_WORDS, value):
        return None
    return next(
        (kind for kind, pattern in CHART_KINDS.items() if re.search(pattern, value)),
        None,
    )


def reshape_kind(question: str) -> str | None:
    """A pure display request, such as 'show that as a pie chart'."""
    if re.search(METRIC_WORDS, fold(question)):
        return None
    return requested_chart(question)


def auto_viz(kind: str, rows: list[dict[str, Any]]) -> VizConfig:
    """Map existing result columns only; ids and sample counts are not values."""
    columns = describe(rows)["columns"]
    shown = [c for c in rows[0] if not c.lower().endswith("id")]
    numeric = [c for c in shown if columns[c]["kind"] == "numeric"]
    values = [c for c in numeric if c != "sample_count"]
    labels = [c for c in shown if columns[c]["kind"] in {"category", "temporal"}]
    x = labels[0] if labels else None
    y, series = values, None
    if kind in {"pie", "donut", "kpi_card", "stacked_bar", "scatter"}:
        y = values[:1]
    if kind == "kpi_card":
        x = None
    elif kind == "scatter":
        x = next((c for c in numeric if c not in y), None)
    elif kind == "stacked_bar":
        series = next((c for c in labels[1:] if columns[c]["kind"] == "category"), None)
    return validate_viz(VizConfig(type=kind, x=x, y=y, series=series), rows)  # type: ignore[arg-type]


def reshaped(
    kind: str,
    rows: list[dict[str, Any]],
    language: str,
) -> tuple[dict[str, Any], str]:
    vi = language == "vi"
    names = {k: v[0 if vi else 1] for k, v in KIND_NAMES.items()}
    if kind == "table":
        return TABLE.model_dump(), (
            "Đã hiển thị kết quả trước dưới dạng bảng."
            if vi
            else "Showing the previous result as a table."
        )
    try:
        return auto_viz(kind, rows).model_dump(), (
            f"Đã hiển thị kết quả trước dưới dạng {names[kind]}."
            if vi
            else f"Showing the previous result as a {names[kind]}."
        )
    except ValueError:
        pass
    if kind != "bar":
        try:
            return auto_viz("bar", rows).model_dump(), (
                f"Kết quả này không phù hợp với {names[kind]} (ví dụ quá nhiều nhóm), "
                "nên tôi hiển thị dạng biểu đồ cột."
                if vi
                else f"This result does not fit a {names[kind]} (for example too many "
                "categories), so I am showing a bar chart."
            )
        except ValueError:
            pass
    hint = {
        "stacked_bar": (
            " Cột chồng cần hai chiều, ví dụ: doanh thu theo tháng và khu vực "
            "dạng cột chồng.",
            " A stacked bar needs two breakdowns, for example: revenue by month "
            "and territory as a stacked bar.",
        ),
        "kpi_card": (
            " Thẻ KPI chỉ hiển thị được khi kết quả có đúng một giá trị.",
            " A KPI card needs a result with exactly one value.",
        ),
        "scatter": (
            " Biểu đồ phân tán cần hai cột số, ví dụ doanh thu và số bản ghi.",
            " A scatter plot needs two numeric columns.",
        ),
    }.get(kind, ("", ""))[0 if vi else 1]
    return TABLE.model_dump(), (
        f"Kết quả này không phù hợp với {names[kind]}, nên tôi hiển thị dạng bảng."
        f"{hint}"
        if vi
        else f"This result does not fit a {names[kind]}, so I am showing a table."
        f"{hint}"
    )


def chart(
    question: str, rows: list[dict[str, Any]], state: Any, budget: RequestBudget
) -> tuple[dict[str, Any], bool]:
    if not state.settings.external_metadata_enabled and not isinstance(
        state.llm, FakeLLM
    ):
        return TABLE.model_dump(), True
    error = None
    for _ in range(state.settings.llm_max_regenerations + 1):
        try:
            proposal = state.llm.visualize(question, describe(rows), error, budget)
            return validate_viz(proposal, rows).model_dump(), False
        except (ValueError, ValidationError) as invalid:
            error = str(invalid)[:180]
        except (httpx.HTTPError, RuntimeError, KeyError, TypeError):
            break
    return TABLE.model_dump(), True
