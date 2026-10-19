# ruff: noqa: E501
"""End-to-end checks for period comparisons and messages that hold several requests.

    python scripts/verify_compare_multitask.py BASE_URL [--only C1,M3] [--retries 1]

Signs in through the demo buttons' endpoint (needs DEMO_LOGIN_ENABLED) and asks the
questions below. Checks look at structure (status, rows, chart, parts) and, when
NEON/WAREHOUSE credentials are in the environment, at the numbers against the warehouse.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

Check = Callable[[dict[str, Any]], list[str]]


# ---- the server's clock -----------------------------------------------------------
# Relative periods ("tháng trước", "năm nay") count from today in Vietnam, or from the
# last day of the sample data when the server runs with RELATIVE_DATES_FROM=data (set
# the same variable when running this script against such a server).
DATA_END = date(2025, 6, 29)  # last day of the sample data


def reference_date() -> date:
    if os.environ.get("RELATIVE_DATES_FROM", "today") == "data":
        return DATA_END
    return datetime.now(timezone(timedelta(hours=7))).date()


def month_start(day: date, delta: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


TODAY = reference_date()
_NEXT = TODAY + timedelta(days=1)
_MONDAY = TODAY - timedelta(days=TODAY.weekday())
THIS_MONTH = (month_start(TODAY), _NEXT)
LAST_MONTH = (month_start(TODAY, -1), month_start(TODAY))
THIS_QUARTER = (quarter_start(TODAY), _NEXT)
LAST_QUARTER = (month_start(quarter_start(TODAY), -3), quarter_start(TODAY))
BEFORE_LAST_QUARTER = (month_start(LAST_QUARTER[0], -3), LAST_QUARTER[0])
LAST_WEEK = (_MONDAY - timedelta(days=7), _MONDAY)
YESTERDAY = (TODAY - timedelta(days=1), TODAY)
THIS_YEAR = (date(TODAY.year, 1, 1), _NEXT)
LAST_YEAR = (date(TODAY.year - 1, 1, 1), date(TODAY.year, 1, 1))


def iso(window: tuple[date, date]) -> tuple[str, str]:
    return window[0].isoformat(), window[1].isoformat()


def status_for(window: tuple[date, date]) -> str:
    """A period that starts after the last day of the data has nothing to show."""
    return "no_data" if window[0] > DATA_END else "ok"


def relative_status(window: tuple[date, date]) -> tuple[str, ...]:
    return (status_for(window),)


# A free AI plan needs a pause after each model call (LiteRouter: 7 s between them).
AI_REST = float(os.environ.get("AI_REST_SECONDS", "0"))
_last_ai_call = [0.0]


def call(base: str, path: str, body: dict[str, Any], token: str | None = None) -> Any:
    if path == "/api/chat/ask" and AI_REST:
        time.sleep(max(0.0, _last_ai_call[0] + AI_REST - time.monotonic()))
    result = post(base, path, body, token)
    if isinstance(result, dict) and result.get("llm_calls"):
        _last_ai_call[0] = time.monotonic()
    return result


def post(base: str, path: str, body: dict[str, Any], token: str | None = None) -> Any:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "origin": base}
        | ({"authorization": "Bearer " + token} if token else {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read())
        except ValueError:
            return {"status": f"http_{error.code}", "table": []}
    except OSError as error:  # a timeout or a dropped connection fails the case
        return {"status": "technical_failure", "message": str(error), "table": []}


def expect(
    result: dict[str, Any],
    status: str | None = None,
    rows: int | tuple[int, int] | None = None,
    viz: str | None = None,
    has: tuple[str, ...] = (),
    lacks: tuple[str, ...] = (),
    parts: list[dict[str, Any]] | int | None = None,
    columns: tuple[str, ...] = (),
) -> list[str]:
    """Failures for one result (and, when `parts` is a list, for each part)."""
    errors = []
    if status and result.get("status") != status:
        errors.append(f"status {result.get('status')!r}, wanted {status!r}")
    table = result.get("table") or []
    if isinstance(rows, int) and len(table) != rows:
        errors.append(f"{len(table)} rows, wanted {rows}")
    if isinstance(rows, tuple) and not rows[0] <= len(table) <= rows[1]:
        errors.append(f"{len(table)} rows, wanted {rows[0]}..{rows[1]}")
    kind = (result.get("viz_config") or {}).get("type")
    if viz and kind != viz:
        errors.append(f"chart {kind!r}, wanted {viz!r}")
    text = f"{result.get('answer_text') or ''} {result.get('message') or ''}".lower()
    for word in has:
        if word.lower() not in text:
            errors.append(f"text lacks {word!r}")
    for word in lacks:
        if word.lower() in text:
            errors.append(f"text has {word!r}")
    for column in columns:
        if not table or column not in table[0]:
            errors.append(f"no column {column!r}")
    found = result.get("parts") or []
    if isinstance(parts, int) and len(found) != parts:
        errors.append(f"{len(found)} parts, wanted {parts}")
    if isinstance(parts, list):
        if len(found) != len(parts):
            errors.append(f"{len(found)} parts, wanted {len(parts)}")
        for index, (part, spec) in enumerate(zip(found, parts), 2):
            errors += [f"part {index}: {e}" for e in expect(part, **spec)]
    elif parts is None and found:
        errors.append(f"unexpected {len(found)} parts")
    return errors


def truth_revenue(territory: str | None, start: str, end: str) -> float | None:
    """Revenue straight from the warehouse, when credentials are available."""
    host = os.environ.get("WAREHOUSE_HOST")
    password = os.environ.get("WAREHOUSE_PASSWORD")
    if not host or not password:
        return None
    import psycopg

    sql = (
        "SELECT SUM(h.subtotal) FROM sales.salesorderheader h "
        "JOIN sales.salesterritory t ON t.territoryid=h.territoryid "
        "WHERE h.orderdate>=%s AND h.orderdate<%s"
        + (" AND t.name=%s" if territory else "")
    )
    params: tuple[str, ...] = (start, end) + ((territory,) if territory else ())
    with psycopg.connect(
        host=host,
        dbname="Adventureworks",
        user="acbi_ro",
        password=password,
        sslmode=os.environ.get("WAREHOUSE_SSLMODE", "require"),
    ) as connection:
        value = connection.execute(sql, params).fetchone()[0]  # type: ignore[index]
    return float(value)


def revenue_matches(territory: str | None, spans: list[tuple[str, str]]) -> Check:
    def check(result: dict[str, Any]) -> list[str]:
        errors = []
        for row, (start, end) in zip(result.get("table") or [], spans):
            truth = truth_revenue(territory, start, end)
            if truth is not None and abs(float(row["revenue"]) - truth) > 0.01:
                errors.append(
                    f"{row['period']}: {row['revenue']} != warehouse {truth:.4f}"
                )
        return errors

    return check


def case(
    id: str, user: str, lang: str, turns: list[tuple[str, dict[str, Any], Check | None]]
) -> dict[str, Any]:
    return {"id": id, "user": user, "lang": lang, "turns": turns}


PERIODS_2324 = [("2023-01-01", "2024-01-01"), ("2024-01-01", "2025-01-01")]

CASES = [
    # ---- comparisons -----------------------------------------------------------
    case(
        "C1",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu canada năm 2023 và 2024",
                dict(
                    status="ok",
                    rows=2,
                    viz="bar",
                    has=("Canada", "tăng"),
                    columns=("period",),
                ),
                revenue_matches("Canada", PERIODS_2324),
            )
        ],
    ),
    case(
        "C2",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu Canada và France năm 2023 và 2024",
                dict(
                    status="ok",
                    rows=2,
                    viz="bar",
                    columns=("territory", "Năm 2023", "Năm 2024"),
                ),
                None,
            )
        ],
    ),
    case(
        "C3",
        "manager",
        "en",
        [
            (
                "compare revenue Q1 2024 and Q1 2025",
                dict(status="ok", rows=2, viz="bar", has=("Q1 2024", "Q1 2025", "up")),
                revenue_matches(
                    None, [("2024-01-01", "2024-04-01"), ("2025-01-01", "2025-04-01")]
                ),
            )
        ],
    ),
    case(
        "C4",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu quý 1 và quý 2 năm 2025",
                dict(status="ok", rows=2, viz="bar", has=("Quý 1/2025", "Quý 2/2025")),
                revenue_matches(
                    None, [("2025-01-01", "2025-04-01"), ("2025-04-01", "2025-07-01")]
                ),
            )
        ],
    ),
    case(
        "C5",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu tháng 1, tháng 2 và tháng 3 năm 2025",
                dict(
                    status="ok", rows=3, viz="bar", has=("Tháng 1/2025", "Tháng 3/2025")
                ),
                revenue_matches(
                    None,
                    [
                        ("2025-01-01", "2025-02-01"),
                        ("2025-02-01", "2025-03-01"),
                        ("2025-03-01", "2025-04-01"),
                    ],
                ),
            )
        ],
    ),
    case(
        "C6",
        "manager",
        "vi",
        [
            (
                "so sánh sản lượng năm 2023 và 2024",
                dict(status="ok", rows=2, viz="bar", has=("Sản lượng",)),
                None,
            )
        ],
    ),
    case(
        "C7",
        "manager",
        "vi",
        [
            (
                "so sánh tỷ lệ phế phẩm theo nhà máy năm 2023 và 2024",
                dict(
                    status="ok",
                    rows=(2, 4),
                    viz="bar",
                    columns=("factory", "Năm 2023", "Năm 2024"),
                ),
                None,
            )
        ],
    ),
    case(
        "C8",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu theo danh mục sản phẩm năm 2023 và 2024",
                dict(status="ok", rows=(3, 6), viz="bar", columns=("category",)),
                None,
            )
        ],
    ),
    case(
        "C9",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2023 và 2024 theo tháng",
                dict(status="ok", rows=24, viz="line"),
                None,
            )
        ],
    ),
    case(
        "C10",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2023 và 2024 bằng biểu đồ tròn",
                dict(status="ok", rows=2, viz="pie"),
                None,
            )
        ],
    ),
    case(
        "C11",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu của Wakanda năm 2023 và 2024",
                dict(status="needs_clarification", rows=0),
                None,
            )
        ],
    ),
    case(
        "C12",
        "sales",
        "vi",
        [("so sánh sản lượng năm 2023 và 2024", dict(status="denied", rows=0), None)],
    ),
    case(
        "C13",
        "production_a",
        "vi",
        [
            (
                "so sánh sản lượng năm 2023 và 2024",
                dict(status="ok", rows=2, viz="bar"),
                None,
            ),
            ("so sánh doanh thu năm 2023 và 2024", dict(status="denied", rows=0), None),
        ],
    ),
    case(
        "C14",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2024 và 2030",
                dict(status="no_data", rows=0, lacks=("dự báo",)),
                None,
            )
        ],
    ),
    case(
        "C15",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2022, 2023, 2024 và 2025",
                dict(status="ok", rows=4, viz="bar"),
                None,
            )
        ],
    ),
    case(
        "C16",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu quý 1 2024 và năm 2023",
                dict(status="needs_clarification", rows=0),
                None,
            )
        ],
    ),
    case(
        "C17",
        "manager",
        "vi",
        [
            (
                "tăng trưởng doanh thu năm 2024 so với 2023",
                dict(status="ok", rows=2, has=("tăng",)),
                revenue_matches(None, PERIODS_2324),
            )
        ],
    ),
    case(
        "C18",
        "manager",
        "vi",
        [
            (
                "So sánh doanh thu Canada năm 2024 với Canada năm 2023 bằng biểu đồ cột",
                dict(status="ok", rows=2, viz="bar"),
                revenue_matches("Canada", PERIODS_2324),
            )
        ],
    ),
    case(
        "C19",
        "manager",
        "en",
        [
            (
                "How did revenue change between 2023 and 2024 in Germany?",
                dict(status="ok", rows=2, has=("Germany",)),
                revenue_matches("Germany", PERIODS_2324),
            )
        ],
    ),
    case(
        "C20",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu Canada năm 2023 và 2024",
                dict(status="ok", rows=2),
                None,
            ),
            ("vẽ dạng cột", dict(status="ok", viz="bar"), None),
            ("còn Australia thì sao?", dict(status="ok"), None),
        ],
    ),
    case(
        "C21",
        "manager",
        "vi",
        [
            (
                "Doanh thu Canada năm 2024 tăng hay giảm so với năm 2023?",
                dict(status="ok", rows=2, has=("Canada", "tăng")),
                revenue_matches("Canada", PERIODS_2324),
            )
        ],
    ),
    case(
        "C22",
        "manager",
        "vi",
        [
            (
                "Canada và Australia, nước nào tăng trưởng doanh thu cao hơn giữa 2023 và 2024?",
                dict(
                    status="ok",
                    rows=2,
                    columns=("territory", "Năm 2023", "Năm 2024"),
                    has=("Tăng mạnh nhất",),
                ),
                None,
            )
        ],
    ),
    case(
        "C23",
        "manager",
        "vi",
        [
            (
                "so sánh sản lượng của Factory A với Factory B năm 2024",
                dict(status="ok", rows=2, has=("Factory A", "Factory B", "chưa gồm")),
                None,
            )
        ],
    ),
    case(
        "C24",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu quý 2 và quý 3 năm 2024 theo khu vực",
                dict(
                    status="ok",
                    rows=10,
                    viz="bar",
                    columns=("Quý 2/2024", "Quý 3/2024"),
                ),
                None,
            )
        ],
    ),
    case(
        "C25",
        "manager",
        "vi",
        [
            (
                "doanh thu tháng 3/2025 so với tháng 3/2024 của France",
                dict(status="ok", rows=2, has=("France",)),
                None,
            )
        ],
    ),
    case(
        "C26",
        "manager",
        "vi",
        [
            (
                "so sánh tỷ lệ đúng hạn năm 2023, 2024 và 2025",
                dict(status="ok", rows=3, viz="bar", has=("điểm phần trăm",)),
                None,
            )
        ],
    ),
    case(
        "C27",
        "manager",
        "en",
        [
            (
                "compare production output between Factory A and Factory B in 2023 vs 2024",
                dict(
                    status="ok",
                    rows=(2, 4),
                    columns=("factory", "Year 2023", "Year 2024"),
                ),
                None,
            )
        ],
    ),
    case(
        "C28",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2023 và 2024 của tất cả khu vực",
                dict(status="ok", rows=10, viz="bar"),
                None,
            )
        ],
    ),
    case(
        "C29",
        "manager",
        "en",
        [
            (
                "revenue Q1 2025 vs Q1 2024 vs Q1 2023",
                dict(status="ok", rows=3, viz="bar"),
                None,
            )
        ],
    ),
    case(
        "C30",
        "manager",
        "vi",
        [
            (
                "so sánh lợi nhuận năm 2023 và 2024",
                dict(status="needs_clarification", rows=0),
                None,
            ),
            ("so sánh doanh thu năm 2023 và 2024 theo khách hàng", dict(rows=0), None),
        ],
    ),
    # ---- several requests in one message --------------------------------------
    case(
        "M1",
        "manager",
        "vi",
        [
            (
                "Doanh thu năm 2024 theo khu vực, sản lượng theo nhà máy năm 2024 và tỷ lệ phế phẩm quý trước",
                dict(
                    status="ok",
                    parts=[dict(status="ok"), dict(status=status_for(LAST_QUARTER))],
                ),
                None,
            )
        ],
    ),
    case(
        "M2",
        "manager",
        "vi",
        [
            (
                "Doanh thu tháng này và sản lượng tháng này",
                dict(
                    status=status_for(THIS_MONTH),
                    parts=[dict(status=status_for(THIS_MONTH))],
                ),
                None,
            )
        ],
    ),
    case(
        "M3",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu Canada năm 2023 và 2024, và dự báo doanh thu 6 tháng tới",
                dict(status="ok", rows=2, parts=[dict(status="ok", has=("dự báo",))]),
                None,
            )
        ],
    ),
    case(
        "M4",
        "manager",
        "vi",
        [
            (
                "Doanh thu năm 2024, dự báo doanh thu 3 tháng tới, và top 3 khu vực theo doanh thu năm 2024",
                dict(status="ok", parts=[dict(status="ok"), dict(status="ok", rows=3)]),
                None,
            )
        ],
    ),
    case(
        "M5",
        "sales",
        "vi",
        [
            (
                "doanh thu năm 2024 và sản lượng năm 2024",
                dict(status="ok", parts=[dict(status="denied")]),
                None,
            )
        ],
    ),
    case(
        "M6",
        "manager",
        "vi",
        [
            (
                "doanh thu Wakanda năm 2024 và sản lượng năm 2024",
                dict(status="needs_clarification", parts=[dict(status="ok")]),
                None,
            ),
            ("Canada", dict(status="ok", parts=None), None),
        ],
    ),
    case(
        "M7",
        "manager",
        "en",
        [
            (
                "revenue for 2024 by territory, output by factory for 2024, and defect rate last quarter",
                dict(
                    status="ok",
                    parts=[dict(status="ok"), dict(status=status_for(LAST_QUARTER))],
                ),
                None,
            )
        ],
    ),
    case(
        "M8",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2023 và 2024 và so sánh sản lượng năm 2023 và 2024",
                dict(
                    status="ok",
                    rows=2,
                    has=("Doanh thu",),
                    parts=[dict(status="ok", rows=2, has=("Sản lượng",))],
                ),
                None,
            )
        ],
    ),
    case(
        "M9",
        "manager",
        "vi",
        [
            (
                "doanh thu 2024, sản lượng 2024, tỷ lệ phế phẩm 2024, tỷ lệ đúng hạn 2024, doanh thu theo khu vực 2024 và dự báo doanh thu",
                dict(status="ok", parts=[dict(status="ok")] * 4),
                None,
            )
        ],
    ),
    case(
        "M10",
        "manager",
        "vi",
        [
            (
                "Doanh thu năm 2024 theo khu vực và sản lượng năm 2024 theo nhà máy",
                dict(status="ok", parts=[dict(status="ok")]),
                None,
            ),
            ("vẽ dạng cột", dict(status="ok", viz="bar", parts=None), None),
        ],
    ),
    case(
        "M11",
        "production_a",
        "vi",
        [
            (
                "sản lượng năm 2024 và doanh thu năm 2024",
                dict(status="ok", parts=[dict(status="denied")]),
                None,
            )
        ],
    ),
    case(
        "M12",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu năm 2023 và 2024 rồi cho tôi biết khu vực nào cao nhất năm 2024",
                dict(status="ok", rows=2, parts=[dict(status="ok")]),
                None,
            )
        ],
    ),
    case(
        "M13",
        "manager",
        "vi",
        [
            (
                "doanh thu 2024, sản lượng 2024, tỷ lệ phế phẩm 2024, tỷ lệ đúng hạn 2024, doanh thu theo khu vực 2024, doanh thu theo tháng 2024 và dự báo doanh thu",
                dict(
                    status="ok",
                    has=("Chưa thực hiện thêm",),
                    parts=[dict(status="ok")] * 4,
                ),
                None,
            )
        ],
    ),
    case(
        "M14",
        "manager",
        "vi",
        [
            (
                "so sánh doanh thu 2023 và 2024, sau đó dự báo doanh thu và top 3 sản phẩm bán chạy 2024",
                dict(status="ok", rows=2, parts=[dict(status="ok"), dict(status="ok")]),
                None,
            )
        ],
    ),
    case(
        "M15",
        "sales",
        "vi",
        [
            (
                "doanh thu quý trước, tăng trưởng doanh thu quý trước theo khu vực và sản lượng quý trước",
                dict(
                    status=status_for(LAST_QUARTER),
                    parts=[
                        dict(status=status_for(LAST_QUARTER)),
                        dict(status="denied"),
                    ],
                ),
                None,
            )
        ],
    ),
    case(
        "M16",
        "manager",
        "en",
        [
            (
                "compare Canada revenue 2023 vs 2024, forecast revenue, and show revenue by product category for 2024",
                dict(status="ok", rows=2, parts=[dict(status="ok"), dict(status="ok")]),
                None,
            )
        ],
    ),
    case(
        "M17",
        "manager",
        "vi",
        [
            (
                "Doanh thu năm 2024 theo khu vực và tỷ lệ phế phẩm năm 2024",
                dict(status="ok", parts=[dict(status="ok")]),
                None,
            ),
            ("còn Canada thì sao?", dict(status="ok"), None),
        ],
    ),
]


def run_case(base: str, item: dict[str, Any]) -> list[str]:
    login = call(base, "/api/auth/demo-login", {"username": item["user"]})
    token = login.get("access_token")
    if not token:
        return [f"login failed for {item['user']}: {login}"]
    conversation = None
    errors: list[str] = []
    for question, spec, check in item["turns"]:
        body = {"question": question, "language": item["lang"]}
        if conversation:
            body["conversation_id"] = conversation
        result = call(base, "/api/chat/ask", body, token)
        problems = expect(result, **spec)
        if check and not problems:
            problems += check(result)
        if problems:
            shown = (result.get("answer_text") or result.get("message") or "")[:160]
            errors.append(f"{question!r}: " + "; ".join(problems) + f" | {shown!r}")
        conversation = result.get("conversation_id") or conversation
    return errors


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
            errors = run_case(args.base.rstrip("/"), item)
            if not errors:
                break
            time.sleep(3)
        label = "PASS" if not errors else "FAIL"
        print(f"{label} {item['id']}" + (f" (try {attempt + 1})" if attempt else ""))
        for error in errors:
            print("   ", error)
        failed += bool(errors)
    print(f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
