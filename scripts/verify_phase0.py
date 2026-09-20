"""Execute trusted reference SQL as acbi_ro; no model calls or runtime QS claims."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings  # noqa: E402
from app.core.dates import month_start, resolve_period  # noqa: E402
from app.core.warehouse import inspect_anchor, warehouse_engine  # noqa: E402

TABLES = [
    "sales.salesorderheader",
    "sales.salesorderdetail",
    "sales.salesterritory",
    "production.workorder",
    "production.workorderrouting",
    "production.location",
    "production.product",
    "production.productsubcategory",
    "production.productcategory",
    "production.scrapreason",
    "acbi_demo.factory",
    "acbi_demo.location_factory",
]


def parameters(question: dict[str, Any], anchor: date) -> dict[str, Any]:
    result = dict(question["parameters"])
    period = question["period"]
    if period and period != "explicit":
        start, end = resolve_period(period, anchor)
        result.update(start=start, end=end)
        if "sales_growth" in question["metric_ids"]:
            delta = -3 if period == "last_quarter" else -1
            result.update(baseline_start=month_start(start, delta), baseline_end=start)
    return result


def main() -> None:
    os.environ.setdefault("WAREHOUSE_HOST", "localhost")
    settings = Settings()  # type: ignore[call-arg]
    engine = warehouse_engine(settings)
    anchor_info = inspect_anchor(engine, settings.data_as_of)
    anchor = date.fromisoformat(anchor_info["data_as_of"])
    source_path = ROOT / "data/eval/golden_questions.yaml"
    golden = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    dictionary_path = ROOT / "data/business_dictionary/dictionary.yaml"
    dictionary = yaml.safe_load(dictionary_path.read_text(encoding="utf-8"))
    approval_statuses = {
        d["approvalStatus"]
        for m in dictionary["businessMetrics"]
        for d in m["definitions"]
    }
    assert approval_statuses in ({"proposed"}, {"approved"})
    counts: dict[str, int] = {}
    with engine.connect() as connection, connection.begin():
        for table in TABLES:  # Static allowlist only, never user input.
            counts[table] = connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
        date_ranges = [dict(r) for r in connection.execute(text("""
            SELECT 'sales.orderdate' AS field,min(orderdate)::date AS minimum,
                   max(orderdate)::date AS maximum FROM sales.salesorderheader
            UNION ALL
            SELECT 'production.enddate',min(enddate)::date,max(enddate)::date
            FROM production.workorder
        """)).mappings()]
        assert connection.execute(text("""
            SELECT count(*) FROM (
              SELECT salesorderid FROM sales.salesorderdetail
              GROUP BY salesorderid
              HAVING sum(orderqty*unitprice*(1-unitpricediscount))<=0
            ) x
        """)).scalar_one() == 0
        assert connection.execute(text("""
            SELECT count(*) FROM sales.salesorderheader h WHERE NOT EXISTS
              (SELECT 1 FROM sales.salesorderdetail d
               WHERE d.salesorderid=h.salesorderid)
        """)).scalar_one() == 0
        assert connection.execute(text("""
            SELECT count(*) FROM production.location l
            LEFT JOIN acbi_demo.location_factory f
            USING(locationid) WHERE f.locationid IS NULL
        """)).scalar_one() == 0
        factory_mapping = [dict(r) for r in connection.execute(text("""
            SELECT l.locationid,l.name AS production_line,f.factory_id,f.name AS factory
            FROM production.location l
            JOIN acbi_demo.location_factory lf USING(locationid)
            JOIN acbi_demo.factory f USING(factory_id) ORDER BY l.locationid
        """)).mappings()]
        privileges = dict(connection.execute(text("""
            SELECT current_user AS username,
              current_setting('default_transaction_read_only') AS default_read_only,
              current_setting('statement_timeout') AS statement_timeout,
              (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls
               FROM pg_roles WHERE rolname=current_user) AS elevated,
              has_schema_privilege(current_user,
                'humanresources','USAGE') AS can_use_hr_schema,
              has_table_privilege(current_user,
                'production.workorder','UPDATE') AS can_update,
              has_schema_privilege(current_user,'public','CREATE') AS can_create_public
        """)).mappings().one())
        assert not any(
            privileges[k]
            for k in [
                "elevated",
                "can_use_hr_schema",
                "can_update",
                "can_create_public",
            ]
        )

    # Negative checks: permission denied even when default read-only is explicitly
    # disabled. WHERE false and rollback prevent changes even on a bad grant.
    security_checks = {}
    for name, query, expected in [
        (
            "write_privileges_denied",
            "UPDATE production.workorder SET orderqty=orderqty WHERE false",
            "42501",
        ),
        (
            "restricted_hr_denied",
            "SELECT count(*) FROM humanresources.employee",
            "42501",
        ),
    ]:
        try:
            with engine.connect() as connection, connection.begin():
                connection.execute(text("SET TRANSACTION READ WRITE"))
                connection.execute(text(query))
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == expected
            security_checks[name] = "passed"
        else:
            raise AssertionError(f"Security check failed: {name}")
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET LOCAL statement_timeout='100ms'"))
            connection.execute(text("SELECT pg_sleep(0.3)"))
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "57014"
        security_checks["statement_timeout"] = "passed"
    else:
        raise AssertionError("Query timeout not enforced")

    results = []
    for question in golden["questions"]:
        sql = question["reference_sql"]
        if sql is None:
            assert question["expected_status"] in {"denied", "needs_clarification"}
            results.append(
                {
                    "id": question["id"],
                    "reference_sql_executed": False,
                    "data_as_of": anchor_info["data_as_of"],
                    "anchor_source": anchor_info["anchor_source"],
                    "outcome": "contract_only_not_runtime_test",
                }
            )
            continue
        params = parameters(question, anchor)
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = [
                dict(row) for row in connection.execute(text(sql), params).mappings()
            ]
        expected = question["expected_status"]
        assert (len(rows) == 0) == (expected == "no_data"), question["id"]
        if question["user"] == "production_a":
            assert params["factory_id"] == 1 and "w.factory_id=:factory_id" in sql
        results.append(
            {
                "id": question["id"],
                "reference_sql_executed": True,
                "status": expected,
                "data_as_of": anchor_info["data_as_of"],
                "anchor_source": anchor_info["anchor_source"],
                "sql": sql,
                "params": params,
                "metric_versions": question["metric_versions"],
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "row_count": len(rows),
                "rows": rows,
                "outcome": "passed",
            }
        )
    by_id = {r["id"]: r for r in results}
    # Independent reconciliation, not merely "SQL ran without error".
    assert (
        sum(r["revenue"] for r in by_id["GQ07"]["rows"])
        == by_id["GQ01"]["rows"][0]["revenue"]
    )
    output = by_id["GQ13"]["rows"][0]["production_output"]
    assert sum(r["production_output"] for r in by_id["GQ14"]["rows"]) == output
    assert sum(r["production_output"] for r in by_id["GQ15"]["rows"]) == output
    a = next(
        r["production_output"] for r in by_id["GQ15"]["rows"] if r["factory_id"] == 1
    )
    assert by_id["GQ16"]["rows"][0]["production_output"] == a
    allocated = sum(r["revenue"] for r in by_id["GQ27"]["rows"])
    assert abs(allocated - by_id["GQ02"]["rows"][0]["revenue"]) < Decimal("0.01")
    assert all(r["defect_rate"] == 0 for r in by_id["GQ32"]["rows"])
    report = {
        "phase": 0,
        "scope": "reference_data_preparation_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": settings.warehouse_database,
        **anchor_info,
        "date_ranges": date_ranges,
        "row_counts": counts,
        "role": privileges,
        "security_checks": security_checks,
        "factory_mapping": factory_mapping,
        "dictionary_sha256": hashlib.sha256(dictionary_path.read_bytes()).hexdigest(),
        "golden_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "definition_status": approval_statuses.pop(),
        "llm_calls": 0,
        "reconciliations": (
            "territory totals, line/factory totals, Factory A scope, "
            "product allocation, observed zero rate: passed"
        ),
        "query_path_evaluation": {
            "structured_intent": "not implemented in Phase 0",
            "rag_text_to_sql": "not implemented in Phase 0",
            "QS1_QS8": "not claimed; runtime scenarios deferred to their build phases",
        },
        "results": results,
    }
    output = Path(os.environ.get("ACBI_REPORT_DIR", str(ROOT)))
    (output / "data/eval").mkdir(parents=True, exist_ok=True)
    (output / "docs").mkdir(parents=True, exist_ok=True)
    target = output / "data/eval/results.local.json"
    target.write_text(
        json.dumps(report, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        k: v for k, v in report.items() if k not in {"results", "factory_mapping"}
    }
    (output / "docs/phase0-verification.json").write_text(
        json.dumps(summary, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, default=str, ensure_ascii=False, indent=2))
    print(
        f"PASS: {sum(r['reference_sql_executed'] for r in results)} reference queries; "
        f"{sum(not r['reference_sql_executed'] for r in results)} no-SQL contracts."
    )
    engine.dispose()


if __name__ == "__main__":
    main()
