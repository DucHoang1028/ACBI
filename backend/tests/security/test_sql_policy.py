"""Adversarial SQL and scope checks that must never reach the database."""

from dataclasses import replace
from datetime import date

import pytest
from app.ai.client import Intent
from app.history.service import permitted
from app.query.builder import build, prepare
from app.query.validation import SQLPolicyError, parse_select, validate

ANCHOR = date(2025, 6, 29)
REVENUE = "SELECT SUM(subtotal) AS revenue FROM sales.salesorderheader"


def intent(metric: str = "revenue", **changes: object) -> Intent:
    values: dict[str, object] = dict(
        metric_id=metric,
        dimension="none",
        period="last_month",
        start_date=None,
        end_date=None,
        factory_id=None,
        territory=None,
        limit=100,
        needs_clarification=False,
        clarification_question=None,
        zero_scrap_only=False,
    )
    values.update(changes)
    return Intent.model_validate(values)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE sales.salesorderheader",
        "DELETE FROM sales.salesorderheader",
        "UPDATE sales.salesorderheader SET subtotal = 0",
        "INSERT INTO sales.salesterritory(name) VALUES ('x')",
        "TRUNCATE sales.salesorderheader",
        "CREATE TABLE sales.t AS SELECT 1",
        REVENUE + "; DROP TABLE sales.salesorderheader",
        REVENUE + " /* hidden */",
        REVENUE + " -- comment",
        REVENUE + " UNION SELECT 1",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(30)",
        "SELECT * INTO backup FROM sales.salesorderheader",
        "SELECT 1 FROM sales.salesorderheader FOR UPDATE",
        "WITH RECURSIVE r AS (SELECT 1 UNION ALL SELECT 1 FROM r) SELECT * FROM r",
        "SELECT 1 FROM postgres.sales.salesorderheader",
        "COPY sales.salesorderheader TO '/tmp/x'",
        'SELECT 1 FROM "sales"."salesorderheader"; SELECT 2',
    ],
)
def test_structural_gate_rejects_unsafe_statements(sql: str) -> None:
    with pytest.raises(SQLPolicyError):
        parse_select(sql, "manager")


@pytest.mark.parametrize(
    ("role", "sql"),
    [
        ("sales", "SELECT 1 FROM production.workorder"),
        ("sales", "SELECT 1 FROM acbi_demo.factory"),
        ("production", "SELECT 1 FROM sales.salesorderheader"),
        ("production", "SELECT 1 FROM person.person"),
        ("manager", "SELECT 1 FROM person.person"),
        ("manager", "SELECT 1 FROM public.app_users"),
        ("it_admin", REVENUE),
        ("unknown", REVENUE),
    ],
)
def test_tables_outside_the_users_scope_are_rejected(role: str, sql: str) -> None:
    with pytest.raises(SQLPolicyError):
        parse_select(sql, role)


def test_scope_holds_for_a_restricted_table_hidden_in_a_subquery() -> None:
    sql = (
        "SELECT SUM(subtotal) AS revenue FROM sales.salesorderheader "
        "WHERE territoryid IN (SELECT locationid FROM production.location)"
    )
    with pytest.raises(SQLPolicyError):
        parse_select(sql, "sales")


def test_user_text_is_bound_never_concatenated() -> None:
    hostile = "x'; DROP TABLE sales.salesorderheader; --"
    user_intent = intent(territory=hostile)
    plan = build(user_intent, "manager", ANCHOR)
    assert plan.params["territory"] == hostile
    assert hostile not in plan.sql and "DROP" not in plan.sql.upper()
    validated = validate(plan, user_intent, "manager", trusted_template=True)
    assert validated.params["territory"] == hostile
    assert ":territory" in validated.sql


def test_row_limit_is_forced_whatever_the_candidate_asks() -> None:
    user_intent = intent()
    plan = replace(
        prepare(user_intent, "manager", ANCHOR),
        sql=(
            "SELECT SUM(subtotal) AS revenue,COUNT(*) AS sample_count "
            "FROM sales.salesorderheader WHERE orderdate>=:start "
            "AND orderdate<:end HAVING COUNT(*)>0 LIMIT 1000000"
        ),
    )
    validated = validate(plan, user_intent, "manager", generated=True)
    assert validated.sql.rstrip().upper().endswith("LIMIT 100")


@pytest.mark.parametrize(
    "row",
    [
        {"factory_id": None, "domain": "production"},  # sales role, production data
        {"factory_id": 2, "domain": "production"},  # another factory
        {"factory_id": None, "domain": "sales"},  # production role, sales data
    ],
)
def test_saved_results_reopen_only_within_current_scope(row: dict) -> None:
    denied_roles = {
        "sales": {"factory_id": None, "domain": "production"},
        "production": {"factory_id": 2, "domain": "production"},
    }
    assert permitted(row, "it_admin") is False
    if row == denied_roles["sales"]:
        assert permitted(row, "sales") is False
    if row == denied_roles["production"]:
        assert permitted(row, "production") is False
    if row == {"factory_id": None, "domain": "sales"}:
        assert permitted(row, "production") is False
    assert permitted(row, "manager") is True
