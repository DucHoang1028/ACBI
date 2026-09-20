# ruff: noqa: E501
"""User-facing wording for internal refusals; raw error text never reaches the user."""

import logging

logger = logging.getLogger("acbi.chat")


class Explained(ValueError):
    """A limitation already written for the user (for example by the model)."""


PERIOD = (
    "Bạn muốn xem kỳ nào? Ví dụ: tháng này, quý trước, năm 2024 hoặc một khoảng ngày.",
    "Which period do you mean? For example: this month, last quarter, 2024, or a date range.",
)
FRIENDLY: dict[str, tuple[str, str]] = {
    "Specify both start and end dates": (
        "Bạn muốn xem từ ngày nào đến ngày nào? Ví dụ: 01/01/2025 đến 31/03/2025.",
        "Which start and end dates do you mean? For example 2025-01-01 to 2025-03-31.",
    ),
    "Specify a reporting period": PERIOD,
    "Invalid reporting period": (
        "Khoảng thời gian này không hợp lệ hoặc quá dài. Hãy chọn một kỳ ngắn hơn.",
        "That period is invalid or too long. Please choose a shorter one.",
    ),
    "Unknown metric": (
        "Tôi chưa có chỉ số này. Bạn có thể hỏi doanh thu, tăng trưởng doanh thu, "
        "sản lượng hoặc tỷ lệ phế phẩm.",
        "I do not have that metric. Ask about revenue, revenue growth, production "
        "output or defect rate.",
    ),
    "Choose an approved metric": (
        "Bạn muốn xem chỉ số nào? Tôi hỗ trợ doanh thu, tăng trưởng doanh thu, "
        "sản lượng và tỷ lệ phế phẩm.",
        "Which metric do you want? I support revenue, revenue growth, production "
        "output and defect rate.",
    ),
    "This metric definition needs a verified query mapping": (
        "Chỉ số này chưa có cách tính đã được kiểm chứng để truy vấn.",
        "This metric has no verified way to be queried yet.",
    ),
    "Growth requires a complete previous month or quarter": (
        "Tăng trưởng chỉ tính được cho tháng trước hoặc quý trước so với kỳ liền "
        "trước đó.",
        "Growth can only be computed for last month or last quarter against the "
        "period before it.",
    ),
    "Growth alignment for this dimension is not approved": (
        "Tăng trưởng doanh thu hiện chỉ chia được theo khu vực.",
        "Revenue growth can currently be split by territory only.",
    ),
    "Sales growth breakdown is not yet supported": (
        "Tăng trưởng doanh thu hiện chỉ chia được theo khu vực.",
        "Revenue growth can currently be split by territory only.",
    ),
    "This dimension is not defined for the selected metric": (
        "Chỉ số này không chia được theo cách bạn yêu cầu. Hãy thử cách nhóm khác.",
        "This metric cannot be split that way. Try another breakdown.",
    ),
    "Unsupported sales dimension": (
        "Doanh thu chia được theo tháng, ngày, khu vực, sản phẩm hoặc danh mục.",
        "Revenue can be split by month, day, territory, product or category.",
    ),
    "Unsupported production dimension": (
        "Sản lượng và phế phẩm chia được theo tháng, sản phẩm, danh mục, dây chuyền, "
        "nhà máy hoặc lý do phế phẩm.",
        "Output and defects can be split by month, product, category, production "
        "line, factory or scrap reason.",
    ),
    "Choose Factory A, B or C": (
        "Chỉ có Factory A, B và C. Bạn muốn xem nhà máy nào?",
        "Only Factory A, B and C exist. Which factory do you mean?",
    ),
    "Revenue has no factory relationship": (
        "Doanh thu không gắn với nhà máy. Tôi có thể so sánh sản lượng hoặc tỷ lệ "
        "phế phẩm theo nhà máy.",
        "Revenue is not linked to factories. I can compare output or defect rate "
        "by factory.",
    ),
    "Production has no sales territory relationship": (
        "Sản lượng và phế phẩm không gắn với khu vực bán hàng. Bạn có thể hỏi theo "
        "nhà máy hoặc dây chuyền.",
        "Production data is not linked to sales territories. Ask by factory or "
        "production line.",
    ),
    "Zero-scrap filtering requires defect rate by product": (
        "Bộ lọc không phế phẩm chỉ dùng được cho tỷ lệ phế phẩm theo sản phẩm.",
        "The zero-scrap filter only works for defect rate by product.",
    ),
    "Zero-scrap filter requires defect rate by product": (
        "Bộ lọc không phế phẩm chỉ dùng được cho tỷ lệ phế phẩm theo sản phẩm.",
        "The zero-scrap filter only works for defect rate by product.",
    ),
    "Zero scrap only applies to defect rate": (
        "Bộ lọc không phế phẩm chỉ dùng được cho tỷ lệ phế phẩm.",
        "The zero-scrap filter only applies to defect rate.",
    ),
    "This combination of breakdowns is not approved": (
        "Cách chia hai chiều này chưa được hỗ trợ. Doanh thu chia được theo tháng "
        "và khu vực; sản lượng theo tháng, dây chuyền, nhà máy hoặc danh mục.",
        "That two-way breakdown is not supported. Revenue works by month and "
        "territory; output by month, line, factory or category.",
    ),
    "Stacked breakdowns are only approved for revenue and production output": (
        "Cột chồng chỉ dùng được cho doanh thu và sản lượng (tỷ lệ không cộng dồn).",
        "Stacked breakdowns only work for revenue and output (ratios do not add).",
    ),
    "Product revenue cannot be allocated: zero detail denominator": (
        "Doanh thu theo sản phẩm không phân bổ được vì dữ liệu chi tiết đơn hàng "
        "không đầy đủ trong kỳ này.",
        "Revenue by product cannot be allocated because order detail is incomplete "
        "for this period.",
    ),
    "External metadata use needs approval": (
        "Loại câu hỏi này cần dùng dịch vụ AI bên ngoài và chưa được cho phép.",
        "This kind of question needs the external AI service, which is not enabled.",
    ),
    "Approved metadata is insufficient for this request": (
        "Tôi chưa tìm thấy định nghĩa đã duyệt phù hợp với yêu cầu này. "
        "Hãy diễn đạt lại theo doanh thu, sản lượng hoặc tỷ lệ phế phẩm.",
        "I found no approved definition that fits this request. Rephrase it around "
        "revenue, output or defect rate.",
    ),
    "More information is required": (
        "Tôi cần thêm thông tin để trả lời. Hãy nêu chỉ số, khoảng thời gian và "
        "cách chia nhóm.",
        "I need more information. Please state the metric, period and breakdown.",
    ),
}
GENERIC = (
    "Tôi chưa xử lý được yêu cầu này. Hãy nêu rõ chỉ số, khoảng thời gian và cách "
    "chia nhóm, ví dụ: “Doanh thu theo khu vực năm 2024”.",
    "I could not handle that request. Please state the metric, period and "
    "breakdown, for example: “Revenue by territory in 2024”.",
)


def friendly(error: Exception, language: str) -> str:
    """Localised wording for a refusal; unknown internal text is logged, not shown."""
    if isinstance(error, Explained):
        return str(error)
    pair = FRIENDLY.get(str(error))
    if pair is None:
        logger.warning("No user wording for internal message: %s", error)
        pair = GENERIC
    return pair[0] if language == "vi" else pair[1]
