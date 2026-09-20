"""Turn a question plus context into a complete Structured Intent."""

import re
from typing import Any

from app.ai.client import Intent
from app.core.dates import (
    TERRITORIES,
    fold,
    intent_hints,
    is_confirmation,
    single_dimension,
    territory_names,
)


def merged_intent(
    intent: Intent, prior: dict[str, Any] | None, question: str = ""
) -> Intent:
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
        # Small talk or an unclear message must not replay the previous query.
        return intent.model_copy(update={"needs_clarification": True})
    if not hints and is_confirmation(question):
        for turn in reversed((prior or {}).get("turns") or []):
            hints = intent_hints(turn.get("question", ""))
            if hints:
                break
    current.update(hints)
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
        and current.get("metric_id")
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
            clarification_question=(
                current.get("clarification_question")
                if unknown == "dimension"
                else None
            ),
        )
    return Intent.model_validate(current)


UNSUPPORTED_BREAKDOWN = (
    r"\b(?:theo|by|per|moi|tung|each)\s+(nhan vien(?: ban hang)?|khach hang|cua hang|"
    r"kenh|phan khuc|thanh pho|tinh|nha cung cap|vat tu|kho|ca lam viec|salesperson|"
    r"sales person|customer|employee|vendor|supplier|store|city|state|channel|"
    r"segment|warehouse|shift)\b"
)
NOUN_VI = {
    "nhan vien ban hang": "nhân viên bán hàng",
    "nhan vien": "nhân viên",
    "khach hang": "khách hàng",
    "cua hang": "cửa hàng",
    "kenh": "kênh",
    "phan khuc": "phân khúc",
    "thanh pho": "thành phố",
    "tinh": "tỉnh",
    "nha cung cap": "nhà cung cấp",
    "vat tu": "vật tư",
    "kho": "kho",
    "ca lam viec": "ca làm việc",
}
EFFICIENCY = r"\b(?:hieu suat|hieu qua|efficiency|performance)\b"
NAMED_MEASURE = (
    r"\b(?:san luong|phe pham|dung han|tre han|doanh thu|output|defect|on time|"
    r"on-time|revenue)\b"
)


def _unknown_choice(current: dict[str, Any], question: str) -> str | None:
    """A factory or territory outside the data must be asked about, not ignored."""
    folded = fold(question)
    if re.search(r"\b(?:nha may|factory)\s*(?:[d-z]|[4-9]|\d{2,})\b", folded):
        return "factory_id"
    unsupported = re.search(UNSUPPORTED_BREAKDOWN, folded)
    if unsupported:
        current["clarification_question"] = unsupported.group(1)
        return "dimension"
    if re.search(EFFICIENCY, folded) and not re.search(NAMED_MEASURE, folded):
        return "efficiency"  # "performance" has no single approved meaning
    if current.get("territory"):
        names = territory_names(str(current["territory"]))
        if names is None:
            return "territory"
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


LOCAL_BREAKDOWN = (
    r"(?:thang|khu vuc|territor|region|danh muc|nhom san pham|loai san pham|"
    r"categor|san pham|product|day chuyen|production line|nha may|factory|"
    r"ly do|scrap reason|quy|nam|month|year)"
)


def local_intent(question: str) -> Intent | None:
    """Build a complete Structured Intent from unambiguous wording, no model call.

    Returns None whenever the wording needs interpretation, so the model decides.
    """
    value = fold(question)
    hints = intent_hints(question)
    if not hints.get("metric_id") or hints.get("period") in (None, "recently"):
        return None
    wording = re.sub(PERIOD_PHRASES, " ", value)
    if re.search(LOCAL_BLOCK, wording) or re.search(r"\bso voi\b", value):
        return None
    factory_id: int | None = None
    letter = re.search(r"\b(?:nha may|factory)\s*([abc])\b", value)
    if letter:
        factory_id = "abc".index(letter.group(1)) + 1
        value = value.replace(letter.group(0), " ")
    if any(
        not re.match(LOCAL_BREAKDOWN, rest.group(1))
        for rest in re.finditer(r"\b(?:theo|by|per|moi|tung)\s+(.+)", value)
    ):
        return None  # a breakdown we do not recognise: let the model decide
    dimension = str(hints.get("dimension") or single_dimension(value) or "")
    if not dimension or (
        dimension == "none" and re.search(r"\b(?:nha may|factory)\b", value)
    ):
        return None
    if not hints.get("territory") and (
        re.search(r"\banh\b", value)  # "Anh": the country, or just a form of address
        or any(
            re.search(rf"\b(?:{pattern})\b", value) for pattern in TERRITORIES.values()
        )
    ):
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
    vi = language == "vi"
    if "dimension" in intent.missing_fields:
        raw = intent.clarification_question or ""
        what = NOUN_VI.get(raw, raw) if vi else raw
        return (
            f"Tôi chưa chia được theo “{what}”. Có thể chia theo: tháng, khu vực, sản "
            "phẩm, danh mục sản phẩm, dây chuyền, nhà máy hoặc lý do phế phẩm."
            if vi
            else f"I cannot split by “{what}” yet. I can split by month, territory, "
            "product, category, production line, factory or scrap reason."
        )
    if "efficiency" in intent.missing_fields:
        return (
            "“Hiệu suất” có thể hiểu theo nhiều cách. Bạn muốn xem sản lượng, tỷ lệ "
            "phế phẩm hay tỷ lệ hoàn thành đúng hạn của dây chuyền?"
            if vi
            else "“Efficiency” can mean several things. Do you want production output, "
            "defect rate or the on-time completion rate of the line?"
        )
    if "factory_id" in intent.missing_fields:
        return (
            "Chỉ có Factory A, B và C. Bạn muốn xem nhà máy nào?"
            if vi
            else "Only Factory A, B and C exist. Which factory do you mean?"
        )
    if "territory" in intent.missing_fields:
        names = ", ".join(TERRITORIES)
        return (
            f"Tôi không nhận ra khu vực này. Các khu vực có dữ liệu: {names}."
            if vi
            else f"I do not recognise that territory. Available territories: {names}."
        )
    if not intent.metric_id:
        return (
            "Tôi chưa xác định được chỉ số bạn muốn xem, hoặc chỉ số đó chưa có định "
            "nghĩa được duyệt. Bạn có thể hỏi doanh thu, tăng trưởng doanh thu, "
            "sản lượng hoặc tỷ lệ phế phẩm, kèm khoảng thời gian."
            if vi
            else "I could not tell which metric you want, or it has no approved "
            "definition yet. Ask about revenue, revenue growth, production output "
            "or defect rate, with a period."
        )
    if not intent.period or intent.period == "recently":
        return (
            "Bạn muốn xem kỳ nào? Ví dụ: tháng này, quý trước, năm 2024 "
            "hoặc một khoảng ngày."
            if vi
            else "Which period should I use? For example: this month, "
            "last quarter, 2024, or a date range."
        )
    return intent.clarification_question or (
        "Cần thêm một chi tiết để trả lời câu hỏi này."
        if vi
        else "One more detail is needed to answer this question."
    )
