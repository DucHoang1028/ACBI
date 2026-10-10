# ruff: noqa: E501
"""Questions that only the RAG text-to-SQL path can answer (weekly, or daily output/defects).

    python scripts/verify_rag_cases.py BASE_URL [--only R01,R04] [--retries 1]

Signs in through the demo endpoint, asks each question in its own conversation and
checks (1) the answer really went through `rag_text_to_sql`, (2) every bucket and value
against the warehouse (set WAREHOUSE_HOST / WAREHOUSE_PASSWORD / WAREHOUSE_SSLMODE, as
`. .tools/neon-env.sh` does). Refusals must carry no table.
"""

import argparse
import time
from typing import Any

from verify_compare_multitask import call
from verify_hard_cases import sql

Rows = dict[str, float]
FACTORY_A = (
    "w.workorderid IN (SELECT terminal.workorderid FROM ("
    "SELECT DISTINCT ON (workorderid) workorderid,locationid FROM production.workorderrouting "
    "ORDER BY workorderid,operationsequence DESC,locationid) terminal "
    "JOIN acbi_demo.location_factory lf USING(locationid) WHERE lf.factory_id=1)"
)


def truth_output(
    bucket: str, start: str, end: str, factory: bool = False
) -> Rows | None:
    expr = (
        "date_trunc('week',w.enddate)::date" if bucket == "week" else "w.enddate::date"
    )
    rows = sql(
        f"SELECT {expr},SUM(w.orderqty-w.scrappedqty) FROM production.workorder w "
        f"WHERE w.enddate>=%s AND w.enddate<%s{' AND ' + FACTORY_A if factory else ''} GROUP BY 1",
        start,
        end,
    )
    return None if rows is None else {str(d): float(v) for d, v in rows}


def truth_defects(
    bucket: str, start: str, end: str, factory: bool = False
) -> Rows | None:
    expr = (
        "date_trunc('week',w.enddate)::date" if bucket == "week" else "w.enddate::date"
    )
    rows = sql(
        f"SELECT {expr},SUM(w.scrappedqty)::numeric/NULLIF(SUM(w.orderqty),0) "
        "FROM production.workorder w WHERE w.enddate>=%s AND w.enddate<%s"
        f"{' AND ' + FACTORY_A if factory else ''} GROUP BY 1",
        start,
        end,
    )
    return None if rows is None else {str(d): float(v or 0) for d, v in rows}


def truth_revenue(
    bucket: str, start: str, end: str, territory: str | None = None
) -> Rows | None:
    expr = (
        "date_trunc('week',h.orderdate)::date"
        if bucket == "week"
        else "h.orderdate::date"
    )
    rows = sql(
        f"SELECT {expr},SUM(h.subtotal) FROM sales.salesorderheader h "
        "JOIN sales.salesterritory t ON t.territoryid=h.territoryid "
        "WHERE h.orderdate>=%s AND h.orderdate<%s AND (%s::text IS NULL OR t.name=%s) GROUP BY 1",
        start,
        end,
        territory,
        territory,
    )
    return None if rows is None else {str(d): float(v) for d, v in rows}


MAR = ("2025-03-01", "2025-04-01")
Q1 = ("2025-01-01", "2025-04-01")
CASES: list[dict[str, Any]] = [
    {
        "id": "R01",
        "q": "Doanh thu theo tuần trong tháng 3 năm 2025",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", *MAR),
    },
    {
        "id": "R02",
        "q": "Sản lượng theo tuần trong quý 1 năm 2025",
        "metric": "production_output",
        "bucket": "week",
        "truth": lambda: truth_output("week", *Q1),
    },
    {
        "id": "R03",
        "q": "Sản lượng theo ngày trong tháng 3 năm 2025",
        "metric": "production_output",
        "bucket": "day",
        "truth": lambda: truth_output("day", *MAR),
    },
    {
        "id": "R04",
        "q": "Sản lượng của Factory A theo từng ngày trong tháng 3 năm 2025",
        "metric": "production_output",
        "bucket": "day",
        "truth": lambda: truth_output("day", *MAR, factory=True),
    },
    {
        "id": "R05",
        "q": "Tỷ lệ lỗi theo ngày trong tháng 3 năm 2025",
        "metric": "defect_rate",
        "bucket": "day",
        "truth": lambda: truth_defects("day", *MAR),
    },
    {
        "id": "R06",
        "q": "Tỷ lệ lỗi theo tuần trong quý 1 năm 2025",
        "metric": "defect_rate",
        "bucket": "week",
        "truth": lambda: truth_defects("week", *Q1),
    },
    {
        "id": "R07",
        "q": "Doanh thu của Canada theo tuần trong tháng 3 năm 2025",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", *MAR, "Canada"),
    },
    {
        "id": "R08",
        "q": "Sản lượng Factory A theo tuần trong quý 1 năm 2025",
        "metric": "production_output",
        "bucket": "week",
        "truth": lambda: truth_output("week", *Q1, factory=True),
    },
    {
        "id": "R09",
        "q": "Doanh thu theo tuần trong quý 1 năm 2025",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", *Q1),
    },
    {
        "id": "R10",
        "q": "Sản lượng theo ngày trong tháng 3 năm 2025",
        "role": "production_a",
        "metric": "production_output",
        "bucket": "day",
        "truth": lambda: truth_output("day", *MAR, factory=True),
    },
    {
        "id": "R11",
        "q": "Sản lượng theo tuần trong quý 1 năm 2025",
        "role": "sales",
        "status": "denied",
    },
    {
        "id": "R12",
        "q": "Doanh thu theo tuần trong tháng 3 năm 2025",
        "role": "production_a",
        "status": "denied",
    },
    {
        "id": "R13",
        "q": "Doanh thu của Factory A theo tuần trong tháng 3 năm 2025",
        "status": "needs_clarification",
    },
    {"id": "R14", "q": "Sản lượng theo tuần trong năm 2030", "status": "no_data"},
    {
        "id": "R15",
        "q": "Weekly revenue in Q4 2024",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", "2024-10-01", "2025-01-01"),
    },
    {
        "id": "R16",
        "q": "Doanh thu theo tuần của Australia trong quý 4 năm 2024",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", "2024-10-01", "2025-01-01", "Australia"),
    },
    {
        "id": "R17",
        "q": "Tỷ lệ lỗi theo ngày của Factory A trong tháng 3 năm 2025",
        "metric": "defect_rate",
        "bucket": "day",
        "truth": lambda: truth_defects("day", *MAR, factory=True),
    },
    {
        "id": "R18",
        "q": "Sản lượng theo ngày trong tháng 12 năm 2024",
        "metric": "production_output",
        "bucket": "day",
        "truth": lambda: truth_output("day", "2024-12-01", "2025-01-01"),
    },
    {
        "id": "R19",
        "q": "Cho tôi sản lượng từng tuần của quý 2 năm 2024",
        "metric": "production_output",
        "bucket": "week",
        "truth": lambda: truth_output("week", "2024-04-01", "2024-07-01"),
    },
    {
        "id": "R20",
        "q": "Tỷ lệ lỗi từng tuần trong tháng 1 năm 2025",
        "metric": "defect_rate",
        "bucket": "week",
        "truth": lambda: truth_defects("week", "2025-01-01", "2025-02-01"),
    },
    {
        "id": "R21",
        "q": "Doanh thu mỗi tuần trong năm 2024",
        "metric": "revenue",
        "bucket": "week",
        "truth": lambda: truth_revenue("week", "2024-01-01", "2025-01-01"),
    },
    {
        "id": "R22",
        "q": "Sản lượng theo ngày trong năm 2024",
        "metric": "production_output",
        "bucket": "day",
        "truth": lambda: truth_output("day", "2024-01-01", "2025-01-01"),
    },
    {
        "id": "R23",
        "q": "Tỷ lệ lỗi theo tuần trong quý 1 năm 2025",
        "role": "production_a",
        "metric": "defect_rate",
        "bucket": "week",
        "truth": lambda: truth_defects("week", *Q1, factory=True),
    },
    {
        "id": "R24",
        "q": "Sản lượng Factory A theo tuần trong tháng 6 năm 2025",
        "metric": "production_output",
        "bucket": "week",
        "truth": lambda: truth_output("week", "2025-06-01", "2025-06-30", factory=True),
    },
]


def login(base: str, role: str) -> str:
    return str(call(base, "/api/auth/demo-login", {"username": role})["access_token"])


def check(result: dict[str, Any], item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    table = result.get("table") or []
    status = result.get("status")
    wanted = item.get("status", "ok")
    if status != wanted:
        return [
            f"status {status!r}, wanted {wanted!r}: {(result.get('answer_text') or result.get('message') or '')[:140]}"
        ]
    if wanted != "ok":
        if table:
            errors.append(f"{len(table)} rows on a {wanted} answer")
        return errors
    if result.get("query_path") != "rag_text_to_sql":
        errors.append(f"path {result.get('query_path')!r}, not the RAG path")
    expected = item["truth"]()
    if expected is None:
        errors.append("no warehouse access: figures not checked")
        return errors
    bucket, metric = item["bucket"], item["metric"]
    got = {str(r.get(bucket)): r.get(metric) for r in table}
    if set(got) != set(expected):
        errors.append(
            f"buckets differ: {len(got)} returned, {len(expected)} expected; missing {sorted(set(expected) - set(got))[:3]}, extra {sorted(set(got) - set(expected))[:3]}"
        )
    for key in sorted(set(got) & set(expected)):
        value = float(got[key])
        tolerance = 1e-6 if metric == "defect_rate" else 0.01
        if abs(value - expected[key]) > tolerance:
            errors.append(f"{key}: got {value}, warehouse {expected[key]}")
            break
    return errors


def run_case(base: str, item: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    started = time.time()
    result = call(
        base,
        "/api/chat/ask",
        {"question": item["q"], "language": "vi"},
        login(base, item.get("role", "manager")),
    )
    result["_secs"] = round(time.time() - started, 1)
    return check(result, item), result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--only", default="")
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()
    wanted = {c for c in args.only.split(",") if c}
    failed = 0
    for item in CASES:
        if wanted and item["id"] not in wanted:
            continue
        for attempt in range(args.retries + 1):
            errors, result = run_case(args.base.rstrip("/"), item)
            if not errors:
                break
            time.sleep(10)
        label = "PASS " if not errors else "FAIL "
        extra = f" (try {attempt + 1})" if attempt else ""
        print(
            f"{label}{item['id']}{extra} {item.get('role', 'manager')}: {item['q']} | {result.get('status')} path={result.get('query_path')} ai={result.get('llm_calls')} rows={len(result.get('table') or [])} {result['_secs']}s",
            flush=True,
        )
        for error in errors:
            print("   ", error, flush=True)
        failed += bool(errors)
        time.sleep(10)
    print(f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
