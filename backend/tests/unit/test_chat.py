from datetime import date

import pytest
import sqlglot
from app.ai.client import Intent
from app.query.builder import build
from sqlglot import exp

ANCHOR = date(2025, 6, 29)


def intent(**changes: object) -> Intent:
    values = dict(
        metric_id="revenue",
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


def test_role_and_factory_checks_precede_query() -> None:
    with pytest.raises(PermissionError):
        build(intent(metric_id="defect_rate"), "sales", ANCHOR)
    with pytest.raises(PermissionError):
        build(intent(metric_id="production_output", factory_id=2), "production", ANCHOR)
    plan = build(intent(metric_id="production_output"), "production", ANCHOR)
    assert plan.params["factory_id"] == 1
    assert "w.factory_id=:factory_id" in plan.sql


def test_user_filter_is_bound_not_sql() -> None:
    unsafe = "Northwest'; DROP TABLE sales.salesorderheader; --"
    plan = build(intent(territory=unsafe), "sales", ANCHOR)
    assert plan.params["territory"] == unsafe
    assert unsafe not in plan.sql
    parsed = sqlglot.parse(plan.sql, read="postgres")
    assert len(parsed) == 1 and isinstance(parsed[0], exp.Select)
    assert not list(parsed[0].find_all(exp.Drop, exp.Delete, exp.Update, exp.Insert))


def test_growth_baseline_and_incompatible_dimension() -> None:
    plan = build(intent(metric_id="sales_growth"), "manager", ANCHOR)
    assert plan.params["start"] == date(2025, 5, 1)
    assert plan.params["baseline_start"] == date(2025, 4, 1)
    with pytest.raises(ValueError, match="factory"):
        build(intent(factory_id=1), "manager", ANCHOR)


def test_growth_by_territory_uses_the_trusted_template() -> None:
    from app.ai.client import Intent
    from app.query.builder import supports
    from app.query.validation import validate

    growth = intent(metric_id="sales_growth", dimension="sales_territory")
    assert supports(growth)
    plan = build(growth, "manager", ANCHOR)
    assert "GROUP BY t.territoryid,t.name" in plan.sql
    checked = validate(plan, growth, "manager", trusted_template=True)
    assert checked.params["baseline_start"] == date(2025, 4, 1)
    assert not supports(intent(metric_id="sales_growth", dimension="product"))
    assert isinstance(growth, Intent)
