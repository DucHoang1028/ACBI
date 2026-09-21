"""Answers about the data itself and small talk, grounded in approved metadata.

The model gets only references drawn from the Business Dictionary, the warehouse's
own dimension members and the tables the caller may read, and its reply is dropped
if it quotes a number found in none of them. Nothing here runs a business query.
"""

import logging
import re
from typing import Any

from app.ai.budget import RequestBudget
from app.auth.service import allows
from app.metadata import vocabulary
from app.query.validation import allowed_tables

logger = logging.getLogger("acbi.chat")
NUMBER = re.compile(r"\d[\d.,]*\d|\d")


def last_answer(saved: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the previous answer in this conversation was, for questions about it."""
    if not saved:
        return None
    payload = saved.get("payload") or {}
    sources = payload.get("sources") or {}
    forecast = sources.get("forecast") or {}
    lines = [
        f"Question: {saved.get('question')}",
        f"Metric: {saved.get('metric_id')}",
        f"Rows returned: {len(payload.get('table') or [])}",
        f"Answer text: {payload.get('answer_text')}",
        f"Period: {sources.get('parameters', {}).get('start')} to "
        f"{sources.get('parameters', {}).get('end')} (end exclusive)",
        f"Chart: {(payload.get('viz_config') or {}).get('type')}",
    ]
    if forecast:
        lines.append(
            f"It was a forecast: method {forecast.get('method')} over "
            f"{forecast.get('history_months')} recorded months, "
            f"hold-out error {forecast.get('backtest_mape')}, "
            f"horizon {forecast.get('horizon_months')} months"
        )
    else:
        lines.append("It was recorded data, not a forecast.")
    return {"id": "previous_answer", "text": "\n".join(lines)}


def references(role: str, data_as_of: str) -> list[dict[str, Any]]:
    """Approved metadata visible to this role, described without access terms."""
    vocab = vocabulary.get()
    factory = 1 if role == "production" else None
    metrics = [m for m in vocab.metrics.values() if allows(role, m.domain, factory)]
    dims = {d for m in metrics for d in m.dimensions}
    grains = {"date": {"month", "week", "day"}}
    lines = [f"Number of metrics you can report: {len(metrics)}"]
    for m in metrics:
        lines.append(
            f"- {m.id} ({m.vi} / {m.en}): {m.formula}; unit {m.unit}; "
            f"split by {', '.join(sorted(dims & set(m.dimensions)))}"
        )
    docs = [{"id": "metrics", "text": "\n".join(lines)}]
    member_lines = []
    for dimension in vocab.dimensions.values():
        if dimension.id not in dims:
            continue
        names = list(vocab.members.get(dimension.id, []))
        ids = vocab.member_ids.get(dimension.id, {})
        if role == "production" and dimension.id == "factory":
            names = [n for n in names if ids.get(n) == 1]
        label = f"{dimension.id} ({dimension.vi} / {dimension.en})"
        if dimension.id in grains:
            label += f", grains: {', '.join(sorted(grains[dimension.id]))}"
        detail = ", ".join(
            (
                f"{n} (= {', '.join(dimension.aliases[n])})"
                if dimension.aliases.get(n)
                else n
            )
            for n in names
        )
        member_lines.append(
            f"- {label}" + (f"; {len(names)} members: {detail}" if names else "")
        )
    docs.append({"id": "dimensions", "text": "\n".join(member_lines)})
    coverage = [
        f"- {m.id}: data from {vocab.coverage[m.id][0]} to {vocab.coverage[m.id][1]}"
        for m in metrics
        if m.id in vocab.coverage
    ]
    docs.append(
        {
            "id": "coverage",
            "text": f"Reference date (relative dates count from it): {data_as_of}\n"
            + "\n".join(coverage),
        }
    )
    tables = allowed_tables(role)
    docs.append(
        {
            "id": "tables",
            "text": f"Number of tables you can read: {len(tables)}\n"
            + "\n".join(f"- {t}: {', '.join(cols)}" for t, cols in tables.items()),
        }
    )
    source = (vocab.sources or [{"name": "the company warehouse"}])[0]["name"]
    docs.append(
        {
            "id": "system",
            "text": f"Data source: {source}. Read-only access. Every answer shows its "
            "SQL, parameters, metric versions and retrieval time and is saved. "
            "Results appear as figures, tables and charts (bar, line, pie, donut, "
            "stacked bar, scatter, KPI card). Voice questions are transcribed for "
            "review first. Revenue and production output can be forecast from "
            "recorded months and are then labelled as forecasts. Nothing is "
            "invented: unsupported questions are declined.",
        }
    )
    return docs


def numbers_in(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in NUMBER.findall(text)}


def grounded(reply: str, refs: list[dict[str, Any]], question: str) -> bool:
    """True when every number in the reply appears in the references or the question."""
    allowed = numbers_in(question)
    for doc in refs:
        allowed |= numbers_in(doc["text"])
    return numbers_in(reply) <= allowed | {"0", "1", "100"}


def reply_from_metadata(
    client: Any,
    question: str,
    role: str,
    mode: str,
    data_as_of: str,
    budget: RequestBudget,
    previous: dict[str, Any] | None = None,
) -> str | None:
    refs = references(role, data_as_of)
    if previous:
        refs.append(previous)
    try:
        text = str(client.converse(question, refs, mode, budget)).strip()
    except ValueError:  # malformed model output: use dictionary wording instead
        logger.warning("dialogue reply malformed")
        return None
    if not text or not grounded(text, refs, question):
        logger.warning("dialogue reply dropped: empty or quotes an unknown number")
        return None
    return text


def fallback(mode: str, language: str, role: str) -> str:
    """Plain wording from the dictionary when no grounded reply can be produced."""
    vocab = vocabulary.get()
    names = ", ".join(m.label(language).lower() for m in vocab.metrics.values())
    if language == "vi":
        head = "Tôi chưa hỗ trợ yêu cầu này. " if mode == "limitation" else ""
        return (
            f"{head}Tôi báo cáo được: {names}. Bạn muốn xem chỉ số nào, trong kỳ nào?"
        )
    head = "I cannot do that yet. " if mode == "limitation" else ""
    return f"{head}I can report: {names}. Which metric and period do you want?"
