# ruff: noqa: E501
"""Independent reconciliation of an answered table against the warehouse.

Given one /api/chat/ask response, work out which figures it shows (metric, breakdown,
period, territory or factory filter) and recompute them with a separate, hand-written
SQL query on the read-only warehouse account. Nothing here reuses the application's own
query builder, so a match means two independent routes gave the same number.

    from reconcile_results import reconcile
    verdict = reconcile(question, response, sql)   # sql(query, *params) -> rows | None

Returns None when there is nothing to check (no table, refusal) or a dict with
status "Khớp", "Lệch" or "Chưa đối chiếu", the SQL used and the differences.
"""

import re
from collections.abc import Callable
from typing import Any

METRICS = ("revenue", "production_output", "defect_rate", "on_time_rate")
RATIO = {"defect_rate", "on_time_rate"}
TERMINAL = (
    "terminal AS (SELECT DISTINCT ON (workorderid) workorderid,locationid "
    "FROM production.workorderrouting ORDER BY workorderid,operationsequence DESC,locationid),"
    "fact AS (SELECT w.*,f.factory_id AS fid,COALESCE(f.name,'Unassigned') AS fname "
    "FROM production.workorder w LEFT JOIN terminal t ON t.workorderid=w.workorderid "
    "LEFT JOIN acbi_demo.location_factory lf ON lf.locationid=t.locationid "
    "LEFT JOIN acbi_demo.factory f ON f.factory_id=lf.factory_id)"
)
LABEL_YEAR = re.compile(r"^(?:Năm|Year)\s+(\d{4})$|^(\d{4})$")
LABEL_QUARTER = re.compile(r"^(?:Quý|Quarter|Q)\s*([1-4])\s*[/ ]\s*(\d{4})$")
LABEL_MONTH = re.compile(r"^(?:Tháng|Month)\s*(\d{1,2})\s*/\s*(\d{4})$")


def span_of(label: str) -> tuple[str, str] | None:
    label = label.strip()
    if m := LABEL_YEAR.match(label):
        y = int(m.group(1) or m.group(2))
        return f"{y}-01-01", f"{y + 1}-01-01"
    if m := LABEL_QUARTER.match(label):
        q, y = int(m.group(1)), int(m.group(2))
        start = f"{y}-{3 * q - 2:02d}-01"
        end = f"{y + 1}-01-01" if q == 4 else f"{y}-{3 * q + 1:02d}-01"
        return start, end
    if m := LABEL_MONTH.match(label):
        mo, y = int(m.group(1)), int(m.group(2))
        return f"{y}-{mo:02d}-01", (
            f"{y + 1}-01-01" if mo == 12 else f"{y}-{mo + 1:02d}-01"
        )
    return None


def truth(
    sql: Callable[..., Any],
    metric: str,
    group: str,
    start: str,
    end: str,
    territories: list[str],
    factory: int | None,
) -> tuple[str, list[Any], dict[Any, float] | None]:
    """Reference figures keyed by group. group: none, territory, factory, month, week, day, territory+month."""
    params: list[Any] = [start, end]
    if metric == "revenue":
        date_col, frm = (
            "h.orderdate",
            "sales.salesorderheader h JOIN sales.salesterritory t ON t.territoryid=h.territoryid",
        )
        value = "SUM(h.subtotal)"
        where = f"{date_col}>=%s AND {date_col}<%s"
        if territories:
            where += " AND t.name = ANY(%s)"
            params.append(territories)
        exprs = {
            "none": "'all'",
            "territory": "t.name",
            "month": f"date_trunc('month',{date_col})::date",
            "week": f"date_trunc('week',{date_col})::date",
            "day": f"{date_col}::date",
        }
        prefix = ""
    else:
        date_col, frm, prefix = "enddate", "fact", "WITH " + TERMINAL + " "
        value = {
            "production_output": "SUM(orderqty-scrappedqty)",
            "defect_rate": "SUM(scrappedqty)::numeric/NULLIF(SUM(orderqty),0)",
            "on_time_rate": "(COUNT(*) FILTER (WHERE enddate<=duedate))::numeric/NULLIF(COUNT(*),0)",
        }[metric]
        where = f"{date_col}>=%s AND {date_col}<%s"
        if factory is not None:
            where += " AND fid=%s"
            params.append(factory)
        exprs = {
            "none": "'all'",
            "factory": "fname",
            "month": "date_trunc('month',enddate)::date",
            "week": "date_trunc('week',enddate)::date",
            "day": "enddate::date",
        }
    if group == "territory+month":
        cols = f"t.name,date_trunc('month',{date_col})::date"
    elif group not in exprs:
        return "", [], None
    else:
        cols = exprs[group]
    grouping = "" if group == "none" else f" GROUP BY {cols}"
    query = f"{prefix}SELECT {cols},{value} FROM {frm} WHERE {where}{grouping}"
    rows = sql(query, *params)
    if rows is None:
        return query, params, None
    out: dict[Any, float] = {}
    for r in rows:
        key = tuple(str(x) for x in r[:-1])
        out[key if len(key) > 1 else key[0]] = (
            float(r[-1]) if r[-1] is not None else 0.0
        )
    return " ".join(query.split()), params, out


def close(got: Any, want: float, metric: str) -> bool:
    try:
        g = float(got)
    except (TypeError, ValueError):
        return False
    return abs(g - want) <= (1e-6 if metric in RATIO else 0.011)


def scope_of(
    result: dict[str, Any],
) -> tuple[str | None, str | None, list[str], int | None]:
    p = (result.get("sources") or {}).get("parameters") or {}
    territories = [
        str(v)
        for k, v in p.items()
        if (k == "territory" or k.startswith("territory_")) and isinstance(v, str)
    ]
    factory = p.get("factory_id")
    return (
        p.get("start"),
        p.get("end"),
        territories,
        factory if isinstance(factory, int) else None,
    )


def guess_metric(question: str) -> str | None:
    text = question.lower()
    for words, name in (
        (("doanh thu", "doanh so", "revenue", "sales"), "revenue"),
        (("sản lượng", "san luong", "output", "production"), "production_output"),
        (("phế phẩm", "phe pham", "lỗi", "defect", "scrap"), "defect_rate"),
        (("đúng hạn", "dung han", "on-time", "on time"), "on_time_rate"),
    ):
        if any(w in text for w in words):
            return name
    return None


def reconcile(
    question: str, result: dict[str, Any], sql: Callable[..., Any]
) -> dict[str, Any] | None:
    rows = result.get("table") or []
    if result.get("status") != "ok" or not rows:
        return None
    keys = set(rows[0])
    start, end, territories, factory = scope_of(result)
    metric = next((m for m in METRICS if m in keys), None)
    info: dict[str, Any] = {
        "status": "Chưa đối chiếu",
        "note": "",
        "sql": "",
        "params": [],
        "checked": 0,
        "mismatches": [],
    }

    def done(status: str, note: str = "") -> dict[str, Any]:
        info["status"], info["note"] = status, note
        return info

    # forecast: the recorded history must equal the monthly totals
    if "actual" in keys and "month" in keys:
        text = question.lower()
        metric = (
            "production_output"
            if any(
                w in text for w in ("sản lượng", "san luong", "output", "production")
            )
            else "revenue"
        )
        if not start or not end:
            return done("Chưa đối chiếu", "thiếu tham số kỳ")
        q, par, ref = truth(sql, metric, "month", start, end, territories, factory)
        info["sql"], info["params"] = q, [str(x) for x in par]
        if ref is None:
            return done("Chưa đối chiếu", "không truy cập được kho dữ liệu")
        for r in rows:
            if r.get("actual") is None:
                continue
            info["checked"] += 1
            want = ref.get(str(r["month"]), 0.0)
            if not close(r["actual"], want, metric):
                info["mismatches"].append((r["month"], r["actual"], want))
        return done(
            "Lệch" if info["mismatches"] else "Khớp",
            "đối chiếu phần số liệu đã ghi nhận; giá trị dự báo được kiểm bằng kiểm thử tự động so với statsmodels",
        )

    period_cols = [k for k in keys if span_of(k)]
    if metric is None and period_cols:
        metric = guess_metric(question)
    if metric is None:
        reason = "chiều phân tích không có truy vấn đối chiếu độc lập (sản phẩm, ngành hàng, dây chuyền, lý do phế phẩm, tăng trưởng)"
        return done("Chưa đối chiếu", reason)

    # comparison rows: one row per period
    if "period" in keys:
        info["sql"] = "(mỗi kỳ một câu SQL độc lập)"
        for r in rows:
            sp = span_of(str(r["period"]))
            if not sp:
                return done("Chưa đối chiếu", f"không đọc được nhãn kỳ {r['period']!r}")
            q, par, ref = truth(sql, metric, "none", sp[0], sp[1], territories, factory)
            if ref is None:
                return done("Chưa đối chiếu", "không truy cập được kho dữ liệu")
            info["sql"], info["params"] = q, [str(x) for x in par]
            info["checked"] += 1
            if not close(r[metric], ref.get("all", 0.0), metric):
                info["mismatches"].append((r["period"], r[metric], ref.get("all", 0.0)))
        return done("Lệch" if info["mismatches"] else "Khớp")

    # comparison wide: one column per period, one row per group
    if period_cols:
        group_col = next((c for c in ("territory", "factory") if c in keys), None)
        if not group_col:
            return done(
                "Chưa đối chiếu",
                "chiều so sánh (ví dụ ngành hàng) không có truy vấn đối chiếu độc lập",
            )
        for col in period_cols:
            sp = span_of(col)
            assert sp
            q, par, ref = truth(
                sql,
                metric,
                "territory" if group_col == "territory" else "factory",
                sp[0],
                sp[1],
                [],
                None,
            )
            if ref is None:
                return done("Chưa đối chiếu", "không truy cập được kho dữ liệu")
            info["sql"], info["params"] = q, [str(x) for x in par]
            for r in rows:
                info["checked"] += 1
                want = ref.get(str(r[group_col]), 0.0)
                if not close(r[col], want, metric):
                    info["mismatches"].append((f"{r[group_col]} / {col}", r[col], want))
        if len(period_cols) == 2 and "change_abs" in keys:
            a, b = sorted(period_cols, key=lambda c: span_of(c))
            for r in rows:
                info["checked"] += 1
                if (
                    r[a] is not None
                    and r[b] is not None
                    and abs((float(r[b]) - float(r[a])) - float(r["change_abs"]))
                    > 0.011
                ):
                    info["mismatches"].append(
                        (
                            f"{r.get(group_col)} change_abs",
                            r["change_abs"],
                            float(r[b]) - float(r[a]),
                        )
                    )
        return done(
            "Lệch" if info["mismatches"] else "Khớp",
            "so từng kỳ với kho dữ liệu và kiểm tra phép trừ của cột chênh lệch",
        )

    if not start or not end:
        return done("Chưa đối chiếu", "thiếu tham số kỳ")
    group_key = None
    group = "none"
    if "territory" in keys and "month" in keys:
        group, group_key = "territory+month", ("territory", "month")
    elif "territory" in keys:
        group, group_key = "territory", "territory"
    elif "factory" in keys:
        group, group_key = "factory", "factory"
    else:
        for g in ("month", "week", "day"):
            if g in keys:
                group, group_key = g, g
                break
    if group == "none" and len(keys - {metric, "sample_count"}) > 0:
        return done(
            "Chưa đối chiếu", "chiều phân tích không có truy vấn đối chiếu độc lập"
        )
    q, par, ref = truth(sql, metric, group, start, end, territories, factory)
    info["sql"], info["params"] = q, [str(x) for x in par]
    if ref is None:
        return done("Chưa đối chiếu", "không truy cập được kho dữ liệu")
    limit = ((result.get("sources") or {}).get("parameters") or {}).get("limit")
    for r in rows:
        if isinstance(group_key, tuple):
            key: Any = (str(r[group_key[0]]), str(r[group_key[1]]))
        else:
            key = str(r[group_key]) if group_key else "all"
        info["checked"] += 1
        if key not in ref:
            info["mismatches"].append((key, r[metric], "không có trong kho dữ liệu"))
        elif not close(r[metric], ref[key], metric):
            info["mismatches"].append((key, r[metric], ref[key]))
    said_left_out = any(
        w in str(result.get("answer_text") or "").lower()
        for w in ("chưa gồm", "not included")
    )
    if (
        group != "none"
        and len(rows) < len(ref)
        and not said_left_out
        and not (isinstance(limit, int) and len(rows) >= limit)
    ):
        shown = {
            (
                str(r[group_key])
                if not isinstance(group_key, tuple)
                else (str(r[group_key[0]]), str(r[group_key[1]]))
            )
            for r in rows
        }
        if group_key and not isinstance(group_key, tuple) and metric not in RATIO:
            missing = [k for k in ref if k not in shown and ref[k] != 0]
            if missing:
                info["mismatches"].append(
                    (
                        "thiếu nhóm",
                        len(rows),
                        f"{len(missing)} nhóm có số liệu: {missing[:3]}",
                    )
                )
    note = f"{len(ref)} nhóm trong kho dữ liệu" if group != "none" else ""
    return done("Lệch" if info["mismatches"] else "Khớp", note)
