from datetime import date
from pathlib import Path

import pytest
import sqlglot
import yaml
from app.core.config import Settings
from app.core.dates import resolve_period
from app.metadata.dictionary import approved_metrics, load_dictionary
from pydantic import ValidationError
from sqlglot import exp


@pytest.mark.parametrize(
    "anchor,period,start,end",
    [
        ("2025-06-29", "this_month", "2025-06-01", "2025-06-30"),
        ("2025-01-02", "last_month", "2024-12-01", "2025-01-01"),
        ("2025-01-02", "last_quarter", "2024-10-01", "2025-01-01"),
        ("2024-02-29", "this_month", "2024-02-01", "2024-03-01"),
        ("2025-06-29", "last_30_days", "2025-05-31", "2025-06-30"),
    ],
)
def test_anchor_calendar_boundaries(
    anchor: str, period: str, start: str, end: str
) -> None:
    assert resolve_period(period, date.fromisoformat(anchor)) == (
        date.fromisoformat(start),
        date.fromisoformat(end),
    )


def test_ambiguous_recently_is_not_invented() -> None:
    with pytest.raises(ValueError, match="clarification"):
        resolve_period("recently", date(2025, 6, 29))


def test_approved_metrics_are_available() -> None:
    assert set(approved_metrics(load_dictionary(Path("data")))) == {
        "production_output",
        "defect_rate",
        "revenue",
        "sales_growth",
        "on_time_rate",
    }


def test_warehouse_superuser_is_rejected() -> None:
    with pytest.raises(ValidationError, match="acbi_ro"):
        Settings(
            warehouse_user="postgres", warehouse_password="test", app_db_password="test"
        )


def test_golden_queries_and_no_query_cases() -> None:
    questions = yaml.safe_load(
        Path("data/eval/golden_questions.yaml").read_text(encoding="utf-8")
    )["questions"]
    assert len(questions) >= 30
    assert len({q["id"] for q in questions}) == len(questions)
    for question in questions:
        if question["expected_status"] in {"denied", "needs_clarification"}:
            assert question["reference_sql"] is None
        else:
            statements = sqlglot.parse(question["reference_sql"], read="postgres")
            assert len(statements) == 1
            query = statements[0]
            assert isinstance(query, exp.Select)
            assert query.args.get("limit") is not None
            assert not list(
                query.find_all(exp.Delete, exp.Update, exp.Insert, exp.Create)
            )


def test_deployment_does_not_own_or_publish_warehouse() -> None:
    compose = yaml.safe_load(
        Path("deploy/docker-compose.yml").read_text(encoding="utf-8")
    )
    assert set(compose["services"]) == {"web", "backend", "db"}
    assert "ports" not in compose["services"]["db"]
    assert compose["networks"]["storage"]["internal"] is True
    assert len(compose["volumes"]) == 1
    assert compose["services"]["backend"]["environment"]["WAREHOUSE_USER"] == "acbi_ro"
