"""Turn a question plus context into a complete Structured Intent.

The model interprets the question. The backend then fills only what the model left
empty, resolves dates deterministically, checks every choice against the approved
dictionary and the data's own members, and asks instead of guessing. Vocabulary
(metric names, breakdowns, territory names) comes from the dictionary and the
warehouse, never from this file.
"""

import re
from typing import Any

from app.ai.client import Intent
from app.core.dates import (
    intent_hints,
    is_confirmation,
    single_dimension,
    territory_names,
)
from app.core.text import fold
from app.metadata import vocabulary

DATA_TYPES = {"metric_query", "comparison", "trend", "ranking", "needs_clarification"}
PERIOD_KEYS = ("period", "start_date", "end_date")
FILL_KEYS = ("metric_id", "dimension", "territory", "series_dimension", "limit")


def merged_intent(
    intent: Intent, prior: dict[str, Any] | None, question: str = ""
) -> Intent:
    if intent.intent_type not in DATA_TYPES:
        return intent  # chat, metadata, unsupported and forecast are routed as they are
    slots = dict((prior or {}).get("slots") or {})
    current = intent.model_dump()
    hints = intent_hints(question)
    adds_information = bool(
        hints
        or intent.metric_id
        or intent.period
        or intent.dimension != "none"
        or intent.territory
        or intent.factory_id is not None
        or intent.start_date
        or intent.zero_scrap_only
        or is_confirmation(question)
    )
    if not adds_information:
        # An unclear message must not replay the previous query.
        return intent.model_copy(update={"needs_clarification": True})
    if not hints and is_confirmation(question):
        for turn in reversed((prior or {}).get("turns") or []):
            hints = intent_hints(turn.get("question", ""))
            if hints:
                break
    for key in PERIOD_KEYS:  # calendar wording is resolved by the backend
        if key in hints:
            current[key] = hints[key]
    override = {"metric_id"} if hints.get("metric_override") else set()
    for key in FILL_KEYS:  # the model's reading stands; hints only fill gaps
        if key not in hints:
            continue
        if key == "limit":
            if hints[key] != 100:
                current[key] = hints[key]
        elif key in override or current.get(key) in (None, "none"):
            current[key] = hints[key]
    for field in ("metric_id", "period", "factory_id", "territory"):
        if current[field] is None:
            current[field] = slots.get(field)
    if current["dimension"] == "none" and slots.get("dimension") not in (None, "none"):
        current["dimension"] = slots["dimension"]
    if current["period"] == "explicit" and not hints and intent.period is None:
        for field in ("start_date", "end_date"):
            if current[field] is None:
                current[field] = slots.get(field)
    missing = current["missing_fields"]
    if missing and "request" not in missing:
        unresolved = [
            field
            for field in missing
            if not current.get(field)
            or (field == "period" and current[field] == "recently")
        ]
        current["missing_fields"] = unresolved
        current["needs_clarification"] = bool(unresolved)
        if not unresolved:
            current["clarification_question"] = None
    if (
        "metric_id" in hints
        and current.get("metric_id") == hints["metric_id"]
        and current.get("period")
        and (
            current["period"] != "explicit"
            or (current.get("start_date") and current.get("end_date"))
        )
    ):
        current.update(
            needs_clarification=False,
            clarification_question=None,
            missing_fields=[],
        )
    unknown = _unknown_choice(current, question)
    if unknown:
        current.update(
            needs_clarification=True,
            missing_fields=[unknown],
            clarification_question=None,
        )
    return Intent.model_validate(current)


ALL_WORDS = {"cac", "moi", "tat", "ca", "all", "every", "each", "the", "nhung"}


def _means_every_member(text: str) -> bool:
    """True for a plural such as "các nước" that names the dimension, not a member."""
    dimension = vocabulary.get().dimensions.get("sales_territory")
    if dimension is None:
        return False
    words = {w for phrase in dimension.synonyms for w in phrase.split()}
    tokens = fold(text).replace("|", " ").split()
    return bool(tokens) and all(t in words or t in ALL_WORDS for t in tokens)


def validate_choices(intent: Intent, question: str) -> Intent:
    """Canonical territory names; ask when a factory or territory is not in the data."""
    current = intent.model_dump()
    unknown = _unknown_choice(current, question)
    if unknown:
        current.update(
            needs_clarification=True,
            missing_fields=[unknown],
            clarification_question=None,
        )
    return Intent.model_validate(current)


def _unknown_choice(current: dict[str, Any], question: str) -> str | None:
    """A factory or territory that is not in the data must be asked about."""
    vocab = vocabulary.get()
    if vocab.unknown_member_reference(fold(question)):
        return "factory_id"
    for dimension, _ in vocab.capitalised_after_dimension(question):
        if dimension == "sales_territory" and not current.get("territory"):
            return "territory"  # named after a dimension word, yet no filter was set
        if dimension == "factory" and current.get("factory_id") is None:
            return "factory_id"
    known = set(vocab.ids("factory").values())
    if (
        current.get("factory_id") is not None
        and known
        and (current["factory_id"] not in known)
    ):
        return "factory_id"
    if current.get("territory"):
        names = territory_names(str(current["territory"]))
        if names is None and _means_every_member(str(current["territory"])):
            current["territory"] = None  # "các nước": no filter, one row per member
            if current["dimension"] == "none":
                current["dimension"] = "sales_territory"
            return None
        if names is None:
            return "territory"
        if names:
            current["territory"] = "|".join(names)
    return None


PERIOD_PHRASES = (
    r"\b(?:hom nay|hom qua|tuan nay|tuan truoc|today|yesterday|this week|last week)\b"
)
LOCAL_BLOCK = (
    r"\b(?:top|cao nhat|thap nhat|lon nhat|nho nhat|chi|khong|trung binh|xep hang|"
    r"sap xep|moi nhat|tuan|ngay|week|weekly|daily|average|highest|lowest|only|"
    r"except)\b"
)


def local_intent(question: str) -> Intent | None:
    """Build a complete Structured Intent from unambiguous wording, no model call.

    An optional shortcut (off by default): it returns None whenever the wording needs
    interpretation, so the model decides.
    """
    vocab = vocabulary.get()
    value = fold(question)
    hints = intent_hints(question)
    if not hints.get("metric_id") or hints.get("period") in (None, "recently"):
        return None
    wording = re.sub(PERIOD_PHRASES, " ", value)
    if re.search(LOCAL_BLOCK, wording) or re.search(r"\bso voi\b", value):
        return None
    if vocab.unknown_member_reference(value):
        return None
    members = vocab.match_members(value)
    factory_id: int | None = None
    if "factory" in members:
        factory_id = vocab.ids("factory").get(members["factory"][0])
    dims = vocab.match_dimensions(wording)
    if (
        any(rest for rest in re.findall(r"\b(?:theo|by|per)\s+\w+", wording))
        and not dims
    ):
        return None  # a breakdown the dictionary does not know: let the model decide
    dimension = str(hints.get("dimension") or single_dimension(wording) or "")
    if not dimension:
        return None
    return Intent.model_validate(
        {
            "metric_id": hints["metric_id"],
            "dimension": dimension,
            "period": hints["period"],
            "start_date": hints.get("start_date"),
            "end_date": hints.get("end_date"),
            "factory_id": factory_id,
            "territory": hints.get("territory"),
            "limit": hints.get("limit", 100),
            "needs_clarification": False,
            "clarification_question": None,
            "zero_scrap_only": False,
            "series_dimension": hints.get("series_dimension", "none"),
        }
    )


def clarification_text(intent: Intent, language: str) -> str:
    """Wording for an unresolved request; the model's own question comes first."""
    vi = language == "vi"
    vocab = vocabulary.get()
    if "factory_id" in intent.missing_fields:
        names = ", ".join(vocab.members.get("factory", []))
        return (
            f"Tôi không có nhà máy đó. Các nhà máy có dữ liệu: {names}."
            if vi
            else f"I have no such factory. Available factories: {names}."
        )
    if "territory" in intent.missing_fields:
        names = ", ".join(vocab.members.get("sales_territory", []))
        return (
            f"Tôi không nhận ra khu vực này. Các khu vực có dữ liệu: {names}."
            if vi
            else f"I do not recognise that territory. Available territories: {names}."
        )
    if intent.clarification_question:
        return intent.clarification_question
    if not intent.metric_id:
        names = ", ".join(m.label(language).lower() for m in vocab.metrics.values())
        return (
            f"Bạn muốn xem chỉ số nào? Tôi hỗ trợ: {names}."
            if vi
            else f"Which metric do you want? I support: {names}."
        )
    if not intent.period or intent.period == "recently":
        return (
            "Bạn muốn xem kỳ nào? Ví dụ: tháng này, quý trước, năm 2024 "
            "hoặc một khoảng ngày."
            if vi
            else "Which period should I use? For example: this month, "
            "last quarter, 2024, or a date range."
        )
    return (
        "Cần thêm một chi tiết để trả lời câu hỏi này."
        if vi
        else "One more detail is needed to answer this question."
    )
