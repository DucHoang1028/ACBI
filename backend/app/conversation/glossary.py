# ruff: noqa: E501
"""Small talk and definitions, answered from the Business Dictionary and fixed
vocabulary. No query runs and no model is called, so nothing can be invented."""

import re
from typing import Any

from app.core.dates import METRIC_WORDS, TERRITORIES, date_hints, fold
from app.presentation.analysis import METRIC_NAMES

TERRITORY_VI = {
    "Canada": "Canada",
    "France": "Pháp",
    "Germany": "Đức",
    "Australia": "Úc",
    "United Kingdom": "Anh (Vương quốc Anh)",
    "Northwest": "Tây Bắc (Hoa Kỳ)",
    "Northeast": "Đông Bắc (Hoa Kỳ)",
    "Southwest": "Tây Nam (Hoa Kỳ)",
    "Southeast": "Đông Nam (Hoa Kỳ)",
    "Central": "Trung tâm (Hoa Kỳ)",
}
METRIC_TERMS = {
    "revenue": r"doanh thu|doanh so|revenue",
    "sales_growth": r"tang truong|growth",
    "production_output": r"san luong|production output",
    "defect_rate": r"ty le phe pham|ty le loi|phe pham|defect rate|scrap rate",
    "on_time_rate": r"dung han|hoan thanh dung han|on[- ]time",
}
UNITS = {
    "source_currency": ("đơn vị tiền tệ của nguồn dữ liệu", "the source currency"),
    "units": ("sản phẩm", "units"),
    "ratio": ("tỷ lệ", "a ratio"),
}
DIMENSION_NAMES = (
    {
        "date": "thời gian (ngày, tháng)",
        "sales_territory": "khu vực",
        "product": "sản phẩm",
        "product_category": "danh mục",
        "production_line": "dây chuyền",
        "factory": "nhà máy",
        "scrap_reason": "lý do phế phẩm",
    },
    {
        "date": "time (day, month)",
        "sales_territory": "territory",
        "product": "product",
        "product_category": "category",
        "production_line": "production line",
        "factory": "factory",
        "scrap_reason": "scrap reason",
    },
)
DEFINE = (
    r"\b(?:la gi|nghia la gi|tieng viet|dich|meaning|means|what is|what does|"
    r"define|dinh nghia|giai thich|cong thuc|tinh nhu the nao|tinh the nao|"
    r"how is .{1,30} calculated)\b"
)
GREETING = r"\b(?:chao|xin chao|hello|hi|hey|alo)\b"
THANKS = r"\b(?:cam on|thanks|thank you|thx)\b"
WHO = r"\b(?:ban la ai|ban ten gi|who are you|what are you)\b"
HELP = (
    r"\b(?:ban lam duoc gi|ban lam duoc nhung gi|ban giup duoc gi|ban co the lam gi|"
    r"lam duoc gi|lam duoc nhung gi|giup gi|biet lam gi|"
    r"huong dan|what can you do|help)\b"
)


def definition(metric: str, dictionary: dict[str, Any], language: str) -> str | None:
    """The approved definition exactly as the dictionary records it."""
    entry = next(
        (m for m in dictionary.get("businessMetrics", []) if m["metricId"] == metric),
        None,
    )
    if not entry:
        return None
    approved = [d for d in entry["definitions"] if d["approvalStatus"] == "approved"]
    if not approved:
        return None
    current = max(approved, key=lambda d: d["version"])
    vi = language == "vi"
    name = METRIC_NAMES[metric][0 if vi else 1].capitalize()
    unit = UNITS.get(current["unit"], (current["unit"], current["unit"]))[
        0 if vi else 1
    ]
    names = DIMENSION_NAMES[0 if language == "vi" else 1]
    dims = ", ".join(names.get(d, d) for d in entry.get("supportedDimensions", []))
    if vi:
        return (
            f"{name}: {current['formula']}. Đơn vị: {unit}. Phiên bản {current['version']}"
            f" (đã duyệt). Chia được theo: {dims}."
        )
    return (
        f"{name}: {current['formula']}. Unit: {unit}. Version {current['version']} "
        f"(approved). Can be split by: {dims}."
    )


def capabilities(language: str) -> str:
    return (
        "Tôi trả lời từ dữ liệu AdventureWorks (chỉ đọc) về doanh thu, tăng trưởng "
        "doanh thu, sản lượng và tỷ lệ phế phẩm, theo tháng, khu vực, sản phẩm, "
        "danh mục, dây chuyền hay nhà máy, dưới dạng số liệu, bảng và biểu đồ. Mỗi "
        "kết quả kèm nguồn và cách tính. Tôi không dự đoán tương lai. Ví dụ: "
        "“Doanh thu theo khu vực năm 2024” hoặc “Tỷ lệ phế phẩm theo lý do quý trước”."
        if language == "vi"
        else "I answer from the AdventureWorks data (read only) on revenue, revenue "
        "growth, production output and defect rate, by month, territory, product, "
        "category, line or factory, as figures, tables and charts, each with its "
        "source and calculation. I do not forecast. Try: “Revenue by territory in "
        "2024” or “Defect rate by reason last quarter”."
    )


def converse(question: str, language: str, dictionary: dict[str, Any]) -> str | None:
    """A grounded reply to small talk or a 'what is X' question, else None."""
    value = fold(question)
    if re.search(METRIC_WORDS, value) and date_hints(question):
        return None  # a real data question that merely mentions a metric and a period
    vi = language == "vi"
    if re.search(DEFINE, value):
        for territory, pattern in TERRITORIES.items():
            if re.search(rf"\b(?:{pattern.split('|(?<')[0]})\b", value):
                return (
                    f"{territory} là {TERRITORY_VI[territory]} — một khu vực bán hàng "
                    f"(sales territory) trong dữ liệu AdventureWorks."
                    if vi
                    else f"{territory} is the sales territory for {TERRITORY_VI[territory]} "
                    "in the AdventureWorks data."
                )
        for metric, pattern in METRIC_TERMS.items():
            if re.search(rf"\b(?:{pattern})\b", value):
                return definition(metric, dictionary, language)
    if len(value.split()) <= 8:
        if re.search(WHO, value) or re.search(HELP, value):
            return capabilities(language)
        if re.search(THANKS, value):
            return (
                "Không có gì! Bạn cần xem thêm số liệu nào nữa không?"
                if vi
                else "You are welcome! Anything else you would like to see?"
            )
        if re.search(GREETING, value) and not re.search(METRIC_WORDS, value):
            return (
                "Xin chào! Bạn muốn xem số liệu nào? Ví dụ: “Doanh thu theo khu vực "
                "năm 2024” hoặc “Sản lượng tháng này”."
                if vi
                else "Hello! What would you like to see? For example: “Revenue by "
                "territory in 2024” or “Production output this month”."
            )
    return None


FORECAST = (
    r"\b(?:du doan|du bao|du tinh|forecast|predict|projection|project(?:ed)?|"
    r"trong tuong lai|(?:thang|quy|nam|tuan) (?:toi|sau)|next (?:week|month|quarter|year)|"
    r"se (?:la|tang|giam|phat trien|ra sao|nhu the nao|the nao|bao nhieu))\b"
)


def asks_forecast(question: str) -> bool:
    """A request about the future: outside what recorded data can support."""
    return bool(re.search(FORECAST, fold(question)))


MATERIALS = (
    r"\b(?:vat tu|nguyen lieu|nguyen vat lieu|thieu hang|ton kho|dinh muc|"
    r"bill of materials|bom|material shortage|raw materials?|stock ?out)\b"
)


def asks_materials(question: str) -> bool:
    return bool(re.search(MATERIALS, fold(question)))


def no_materials(language: str) -> str:
    """Materials and stock cannot be answered: no grant, no future plan in the data."""
    return (
        "Tôi chưa trả lời được về vật tư, định mức hay tồn kho. Tài khoản đọc dữ liệu "
        "chưa được cấp quyền trên bảng định mức (billofmaterials) và tồn kho "
        "(productinventory), và dữ liệu cũng không có lệnh sản xuất nào cho tuần tới "
        "để tính nhu cầu. Nếu cần, chủ hệ thống có thể cấp quyền đọc hai bảng đó và "
        "duyệt định nghĩa “nguy cơ thiếu vật tư”. Hiện tôi xem được sản lượng, tỷ lệ "
        "phế phẩm và tỷ lệ hoàn thành đúng hạn theo sản phẩm, dây chuyền và nhà máy."
        if language == "vi"
        else "I cannot answer about materials, bills of materials or stock yet. The "
        "read-only account has no access to the billofmaterials and productinventory "
        "tables, and the data holds no work orders for next week to derive demand "
        "from. The owner could grant read access and approve a definition of "
        "“material shortage risk”. For now I can report output, defect rate and "
        "on-time completion by product, line and factory."
    )
