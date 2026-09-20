"""Fixed dimension listings and unsupported-combination answers, no model call."""

import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.dates import METRIC_WORDS, fold
from app.presentation.charts import TABLE
from app.presentation.contract import response
from app.query.validation import parse_select

SPECIAL_ROLES = {
    "factories": {"manager", "production"},
    "coverage": {"manager", "sales"},
    "factory_revenue": {"manager", "sales", "production"},
    "territories": {"manager", "sales"},
    "categories": {"manager", "sales", "production"},
    "lines": {"manager", "production"},
}


LISTING = r"\b(?:tat ca|danh sach|liet ke|nhung|co nhung|dang co|list|all|which)\b"


LISTS = {
    "territories": (
        r"\b(?:khu vuc|territor(?:y|ies)|regions?)\b",
        "SELECT name AS territory FROM sales.salesterritory ORDER BY name",
        ("khu vực", "sales territories"),
    ),
    "categories": (
        r"\b(?:danh muc|nhom san pham|product categor(?:y|ies)|categories)\b",
        "SELECT name AS category FROM production.productcategory ORDER BY name",
        ("danh mục sản phẩm", "product categories"),
    ),
    "lines": (
        r"\b(?:day chuyen|production lines?|line san xuat)\b",
        "SELECT name AS production_line FROM production.location ORDER BY name",
        ("dây chuyền sản xuất", "production lines"),
    ),
}


def special_domain(kind: str, role: str) -> str:
    """Access domain a saved listing belongs to, checked again when reopened."""
    if kind in {"factories", "lines"}:
        return "production"
    if kind == "categories" and role == "production":
        return "production"
    return "sales"


def special_kind(question: str) -> str | None:
    value = fold(question)
    asks_factory = "nha may" in value or "factory" in value
    if asks_factory and re.search(
        r"\b(?:bao nhieu|nhung|ten|danh sach|how many|which)\b", value
    ):
        return "factories"
    if re.search(r"\b(?:du lieu|data)\b", value) and re.search(
        r"\b(?:nam|year|20\d\d)\b", value
    ):
        return "coverage"
    if asks_factory and re.search(r"\b(?:doanh thu|doanh so|revenue|sales)\b", value):
        return "factory_revenue"
    if re.search(LISTING, value) and not re.search(METRIC_WORDS, value):
        for kind, (pattern, _, _) in LISTS.items():
            if re.search(pattern, value):
                return kind
    return None


def special_response(
    kind: str,
    engine: Any,
    role: str,
    language: str,
    anchor: date,
    anchor_source: str,
    request_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    vi = language == "vi"
    if kind == "factory_revenue":
        message = (
            "Doanh thu không thể phân theo nhà máy trong AdventureWorks vì đơn bán "
            "hàng không liên kết với factory. Tôi có thể so sánh sản lượng hoặc tỷ lệ "
            "phế phẩm của Factory A, B và C."
            if vi
            else "Revenue cannot be split by factory because sales orders are not "
            "linked to factories. I can compare production output or defect rate "
            "for Factory A, B and C."
        )
        result = response("needs_clarification", message, request_id, conversation_id)
        result["llm_calls"] = 0
        return result
    if kind == "factories":
        sql = (
            "SELECT name AS factory FROM acbi_demo.factory "
            + ("WHERE factory_id = 1 " if role == "production" else "")
            + "ORDER BY factory_id"
        )
    elif kind == "coverage":
        sql = (
            "SELECT DATE_TRUNC('year', orderdate)::date AS year,COUNT(*) AS orders "
            "FROM sales.salesorderheader GROUP BY 1 ORDER BY 1"
        )
    else:
        sql = LISTS[kind][1]
        if kind == "lines" and role == "production":
            sql = (
                "SELECT l.name AS production_line FROM production.location l "
                "JOIN acbi_demo.location_factory lf USING(locationid) "
                "WHERE lf.factory_id = 1 ORDER BY l.name"
            )
    parse_select(sql, role)  # the same structural gate as every other statement
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(
            text("SELECT set_config('statement_timeout', '15000', true)")
        )
        rows = [dict(row) for row in connection.execute(text(sql)).mappings()]
    if kind == "coverage":
        rows = [{**row, "year": row["year"].year} for row in rows]
    if kind == "factories":
        names = ", ".join(str(row["factory"]) for row in rows)
        answer = (
            f"Database có {len(rows)} nhà máy: {names}."
            if vi
            else f"The database has {len(rows)} factories: {names}."
        )
    elif kind == "coverage":
        years = ", ".join(str(row["year"]) for row in rows)
        answer = (
            f"Dữ liệu đơn bán hàng có các năm: {years}."
            if vi
            else f"Sales-order data is available for: {years}."
        )
    else:
        noun = LISTS[kind][2][0 if vi else 1]
        names = ", ".join(str(next(iter(row.values()))) for row in rows)
        answer = (
            f"Có {len(rows)} {noun}: {names}."
            if vi
            else f"There are {len(rows)} {noun}: {names}."
        )
    result = response("ok", "Results found", request_id, conversation_id)
    result.update(
        answer_text=answer,
        table=rows,
        viz_config=TABLE.model_dump(),
        chart_fallback=True,
        sources={
            "source": "Adventureworks",
            "sql": sql,
            "parameters": {},
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "metric_versions": {},
            "data_as_of": anchor.isoformat(),
            "anchor_source": anchor_source,
            "references": [],
        },
        llm_calls=0,
    )
    return result
